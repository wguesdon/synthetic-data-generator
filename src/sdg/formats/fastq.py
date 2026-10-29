"""FASTQ reads: a folder with one file, or one pair of files, per sample.

Human reads hold germline variants that identify a person, so no real read
ever goes into a config or a report (privacy rule 9). A profile records the
read length, the quality profile per position, the header style, the index
length and the pairing. Generation samples reads from a public reference FASTA,
or from a random genome when the config names none, and adds base errors from
the drawn quality scores.

A FASTQ dataset draws on its own in both modes. In the model mode it writes one
file or pair per design sample, so the file names match the other datasets.
"""

from __future__ import annotations

import gzip
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import Field, model_validator

from sdg import privacy
from sdg.config_base import DatasetBase, Share
from sdg.design import design_rows
from sdg.matrix import most_common_shape
from sdg.modes.resample import invented_identifiers, quantile_draws
from sdg.report import Fact, TableReport
from sdg.seeding import stream

if TYPE_CHECKING:
    from sdg.generate import Context

FORMATS = ("fastq",)
SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")

#: A file stem that ends with a mate number, such as ``S1_R1_001`` or ``S1_2``.
MATE = re.compile(r"^(?P<sample>.+?)(?P<suffix>[._-]R?(?P<mate>[12])(?:_001)?)$")

#: Header styles, tried in order.
ILLUMINA = re.compile(r"^@[^\s:]+:\d+:[^\s:]+:\d+:\d+:\d+:\d+(?: [12]:[YN]:\d+:(?P<index>\S*))?$")
SRA = re.compile(r"^@[SED]RR\d+\.\d+")

#: Reads that a profile reads from the start of each file.
PROFILE_READS = 10000

#: Most reads per sample that a profile records; a full-size file is rarely needed.
MAX_READS = 100000

#: Reads written per block.
READ_BLOCK = 5000

#: Bases as bytes, and the complement of each byte.
BASES = np.frombuffer(b"ACGT", dtype=np.uint8)
COMPLEMENT = np.arange(256, dtype=np.uint8)
COMPLEMENT[np.frombuffer(b"ACGTNacgtn", dtype=np.uint8)] = np.frombuffer(b"TGCANTGCAN", dtype=np.uint8)

#: Length of the random genome when the config names no reference.
RANDOM_GENOME = 200000

#: Defaults where a profile cannot measure.
DEFAULT_INSERT_MEAN = 300.0
DEFAULT_INSERT_SD = 50.0


class FastqDataset(DatasetBase):
    """A folder of FASTQ files.

    Attributes:
        format: ``fastq``.
        file: Output folder.
        samples: Number of samples in the resample mode.
        sample_pattern: Shape of a sample name, the start of each file name.
        paired: Write a pair of files per sample.
        pair_suffixes: File name suffixes of mate 1 and mate 2, such as ``_R1_001``.
        compressed: Write ``.fastq.gz`` files.
        reads: Reads, or read pairs, per sample.
        read_length: Length of a read before trimming.
        length_quantiles: Quantiles of the read length after trimming, or None
            for reads of one length.
        quality_offset: ASCII offset of the quality scores, 33 or 64.
        quality_means: Mean quality score at each position.
        quality_sds: Standard deviation of the quality score at each position.
        header_style: ``illumina``, ``sra`` or ``generic``.
        index_length: Bases of the index in an Illumina header.
        index_dual: The header has two indexes joined by ``+``.
        reference: Path of a public FASTA file, or None for a random genome.
        library: ``wgs`` samples contigs by length; ``rnaseq`` samples
            transcripts evenly.
        insert_mean: Mean fragment length of a pair.
        insert_sd: Standard deviation of the fragment length.
        gc_share: GC share of the random genome.
        n_share: Share of the bases written as ``N``.
    """

    format: Literal["fastq"]
    samples: int | None = Field(default=None, ge=1)
    sample_pattern: str = "SMPL-99"
    paired: bool = True
    pair_suffixes: tuple[str, str] = ("_R1", "_R2")
    compressed: bool = True
    reads: int = Field(default=10000, ge=1)
    read_length: int = Field(default=150, ge=1)
    length_quantiles: list[float] | None = None
    quality_offset: Literal[33, 64] = 33
    quality_means: list[float] = Field(default_factory=list)
    quality_sds: list[float] = Field(default_factory=list)
    header_style: Literal["illumina", "sra", "generic"] = "illumina"
    index_length: int = Field(default=8, ge=0)
    index_dual: bool = False
    reference: str | None = None
    library: Literal["wgs", "rnaseq"] = "wgs"
    insert_mean: float = Field(default=DEFAULT_INSERT_MEAN, gt=0.0)
    insert_sd: float = Field(default=DEFAULT_INSERT_SD, ge=0.0)
    gc_share: Share = 0.41
    n_share: Share = 0.0

    @model_validator(mode="after")
    def check_reference(self) -> FastqDataset:
        """Check that a named reference file exists.

        Returns:
            The unchanged dataset.

        Raises:
            ValueError: If the reference file is missing.
        """
        if self.reference and not Path(self.reference).expanduser().is_file():
            raise ValueError(f"reference: no FASTA file at {self.reference}")
        return self

    def mode_error(self, mode: str) -> str | None:
        """Require a sample count in the resample mode.

        Args:
            mode: ``resample`` or ``model``.

        Returns:
            The error, or None.
        """
        if mode == "resample" and self.samples is None:
            return "samples: the resample mode needs a sample count"
        return None


DATASET_MODELS = (FastqDataset,)


def read_fasta(path: Path) -> list[np.ndarray]:
    """Read the contigs of a FASTA file, upper case.

    Args:
        path: A plain or gzip FASTA file.

    Returns:
        One byte array per contig.
    """
    opener = gzip.open if privacy.file_suffix(path).endswith(".gz") else open
    contigs, parts = [], []
    with opener(path, "rb") as handle:
        for line in handle:
            if line.startswith(b">"):
                if parts:
                    contigs.append(np.frombuffer(b"".join(parts).upper(), dtype=np.uint8))
                parts = []
            else:
                parts.append(line.strip())
    if parts:
        contigs.append(np.frombuffer(b"".join(parts).upper(), dtype=np.uint8))
    return contigs


def genome(dataset: FastqDataset, seed: int) -> list[np.ndarray]:
    """Load the reference, or draw a random genome with the GC share.

    Args:
        dataset: The dataset.
        seed: The config seed.

    Returns:
        The contigs that reads come from.
    """
    if dataset.reference:
        return read_fasta(Path(dataset.reference).expanduser())
    shares = [(1 - dataset.gc_share) / 2, dataset.gc_share / 2, dataset.gc_share / 2, (1 - dataset.gc_share) / 2]
    rng = stream(seed, dataset.name, "genome")
    return [BASES[rng.choice(4, size=RANDOM_GENOME, p=shares)]]


def reverse_complement(sequences: np.ndarray) -> np.ndarray:
    """Return the reverse complement of each row of bases.

    Args:
        sequences: Bases, reads by positions.

    Returns:
        The reverse complements.
    """
    return COMPLEMENT[sequences][:, ::-1]


def qualities(dataset: FastqDataset, count: int, rng: np.random.Generator) -> np.ndarray:
    """Draw quality scores per position.

    Args:
        dataset: The dataset.
        count: Number of reads.
        rng: Random stream.

    Returns:
        Quality scores, reads by positions, from 2 to 41.
    """
    length = dataset.read_length
    means = stretch(dataset.quality_means or [35.0], length)
    sds = stretch(dataset.quality_sds or [3.0], length)
    return np.clip(np.round(rng.normal(means, sds, size=(count, length))), 2, 41).astype(np.uint8)


def stretch(values: list[float], length: int) -> np.ndarray:
    """Stretch a profile over a length by linear interpolation.

    Args:
        values: The profile, one value per position of some read length.
        length: The length to stretch it to.

    Returns:
        ``length`` values. A profile of one value stays flat.
    """
    if len(values) == 1:
        return np.full(length, float(values[0]))
    return np.interp(np.linspace(0, len(values) - 1, length), np.arange(len(values)), values)


def add_errors(sequences: np.ndarray, scores: np.ndarray, n_share: float, rng: np.random.Generator) -> np.ndarray:
    """Change bases at the error rate of their quality, and write some as N.

    Args:
        sequences: Bases, reads by positions.
        scores: Quality scores, changed in place where a base becomes N.
        n_share: Share of the bases written as N.
        rng: Random stream.

    Returns:
        The bases with the errors.
    """
    changed = sequences.copy()
    errors = rng.random(sequences.shape) < 10.0 ** (-scores.astype(float) / 10.0)
    codes = np.searchsorted(BASES, changed[errors])
    changed[errors] = BASES[(np.clip(codes, 0, 3) + rng.integers(1, 4, size=codes.size)) % 4]
    unknown = rng.random(sequences.shape) < n_share
    changed[unknown] = ord("N")
    scores[unknown] = 2
    return changed


def fragments(dataset: FastqDataset, contigs: list[np.ndarray], count: int,
              rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Draw read pairs from the contigs.

    Args:
        dataset: The dataset.
        contigs: The contigs.
        count: Number of reads or pairs.
        rng: Random stream.

    Returns:
        The mate 1 and the mate 2 bases, reads by positions.
    """
    length = dataset.read_length
    usable = [contig for contig in contigs if len(contig) >= length]
    if not usable:
        raise ValueError(f"no contig of the reference is as long as a read ({length} bases)")
    sizes = np.array([len(contig) for contig in usable], dtype=float)
    weights = sizes / sizes.sum() if dataset.library == "wgs" else np.full(len(usable), 1 / len(usable))
    chosen = rng.choice(len(usable), size=count, p=weights)
    insert = np.round(rng.normal(dataset.insert_mean, dataset.insert_sd, size=count)).astype(int)
    first = np.empty((count, length), dtype=np.uint8)
    second = np.empty((count, length), dtype=np.uint8)
    offsets = np.arange(length)
    for index, contig in enumerate(usable):
        rows = np.flatnonzero(chosen == index)
        if not rows.size:
            continue
        spans = np.clip(insert[rows], length, len(contig))
        starts = (rng.random(rows.size) * (len(contig) - spans + 1)).astype(int)
        left = contig[starts[:, None] + offsets]
        right = reverse_complement(contig[(starts + spans - length)[:, None] + offsets])
        forward = rng.random(rows.size) < 0.5
        first[rows] = np.where(forward[:, None], left, right)
        second[rows] = np.where(forward[:, None], right, left)
    return first, second


def headers(dataset: FastqDataset, sample_index: int, start: int, count: int, mate: int,
            index: str) -> list[bytes]:
    """Write the header lines of a block of reads.

    Args:
        dataset: The dataset.
        sample_index: Position of the sample, from 0.
        start: Number of the first read of the block, from 1.
        count: Reads in the block.
        mate: 1 or 2.
        index: Index text of the sample.

    Returns:
        One header line per read.
    """
    if dataset.header_style == "illumina":
        lane = sample_index % 8 + 1
        return [f"@SDG001:1:FLOWCELLX:{lane}:{1101 + number // 100000}:{1000 + number % 30000}:"
                f"{1000 + number // 30000 % 30000} {mate}:N:0:{index}".encode()
                for number in range(start, start + count)]
    if dataset.header_style == "sra":
        return [f"@SRR0000001.{number} {number}/{mate}".encode() for number in range(start, start + count)]
    return [f"@read_{number}/{mate}".encode() for number in range(start, start + count)]


def sample_index_text(dataset: FastqDataset, rng: np.random.Generator) -> str:
    """Draw the index text of one sample for its Illumina headers.

    Args:
        dataset: The dataset.
        rng: Random stream of the sample.

    Returns:
        The index, or two indexes joined by ``+``.
    """
    first = BASES[rng.integers(0, 4, size=dataset.index_length)].tobytes().decode()
    if not dataset.index_dual:
        return first
    return first + "+" + BASES[rng.integers(0, 4, size=dataset.index_length)].tobytes().decode()


def record_block(names: list[bytes], sequences: np.ndarray, scores: np.ndarray, lengths: np.ndarray,
                 offset: int) -> bytes:
    """Write a block of FASTQ records.

    Args:
        names: Header lines.
        sequences: Bases, reads by positions.
        scores: Quality scores, reads by positions.
        lengths: Length of each read after trimming.
        offset: ASCII offset of the quality scores.

    Returns:
        The records as bytes.
    """
    letters = (scores + offset).astype(np.uint8)
    return b"".join(name + b"\n" + sequences[row, :size].tobytes() + b"\n+\n" + letters[row, :size].tobytes()
                    + b"\n" for row, (name, size) in enumerate(zip(names, lengths, strict=True)))


def sample_list(dataset: FastqDataset, context: Context) -> list[str]:
    """Return the sample names: the design samples in the model mode.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The sample names.
    """
    config = context.config
    if config.mode == "model":
        return [row.sample.sample_id for row in design_rows(config)]
    return invented_identifiers(dataset.sample_pattern, dataset.samples, stream(config.seed, dataset.name, "samples"))


def open_output(path: Path, compressed: bool) -> Any:
    """Open an output file, with a fixed gzip time so reruns give the same bytes.

    Args:
        path: The file.
        compressed: Write gzip.

    Returns:
        A binary file handle.
    """
    if compressed:
        return gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0)
    return path.open("wb")


def generate(dataset: FastqDataset, context: Context) -> list[Path]:
    """Write the FASTQ files of every sample.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The files written.
    """
    seed = context.config.seed
    contigs = genome(dataset, seed)
    folder = context.out_dir / dataset.file
    folder.mkdir(parents=True, exist_ok=True)
    extension = ".fastq.gz" if dataset.compressed else ".fastq"
    paths = []
    for sample_index, sample in enumerate(sample_list(dataset, context)):
        rng = stream(seed, dataset.name, sample)
        index = sample_index_text(dataset, rng)
        mates = [1, 2] if dataset.paired else [1]
        targets = [folder / f"{sample}{dataset.pair_suffixes[mate - 1] if dataset.paired else ''}{extension}"
                   for mate in mates]
        handles = [open_output(target, dataset.compressed) for target in targets]
        try:
            for start in range(1, dataset.reads + 1, READ_BLOCK):
                count = min(READ_BLOCK, dataset.reads - start + 1)
                pairs = fragments(dataset, contigs, count, rng)
                lengths = (np.full(count, dataset.read_length) if dataset.length_quantiles is None else
                           np.clip(np.round(quantile_draws(dataset.length_quantiles, rng.random(count))), 1,
                                   dataset.read_length).astype(int))
                for mate, handle in zip(mates, handles, strict=True):
                    scores = qualities(dataset, count, rng)
                    sequences = add_errors(pairs[mate - 1], scores, dataset.n_share, rng)
                    handle.write(record_block(headers(dataset, sample_index, start, count, mate, index),
                                              sequences, scores, lengths, dataset.quality_offset))
        finally:
            for handle in handles:
                handle.close()
        paths += targets
    return paths


def fastq_stem(path: Path) -> str:
    """Return the file name without its FASTQ suffixes.

    Args:
        path: A FASTQ file.

    Returns:
        The name without ``.fastq.gz``, ``.fq`` or the like.
    """
    return path.name[: -len(privacy.file_suffix(path))] if privacy.file_suffix(path) else path.name


def sample_files(paths: list[Path]) -> dict[str, list[Path]]:
    """Group FASTQ files by sample, with the mates in order.

    Args:
        paths: The files.

    Returns:
        Each sample name mapped to its file, or its mate 1 and mate 2 files.
    """
    groups: dict[str, dict[int, Path]] = {}
    for path in paths:
        match = MATE.match(fastq_stem(path))
        if match:
            groups.setdefault(match["sample"], {})[int(match["mate"])] = path
        else:
            groups.setdefault(fastq_stem(path), {})[1] = path
    return {sample: [mates[mate] for mate in sorted(mates)] for sample, mates in groups.items()}


def read_records(path: Path, limit: int) -> tuple[list[tuple[str, str, str]], int]:
    """Read the first records of a FASTQ file, and how many file bytes they took.

    Args:
        path: The file.
        limit: Most records to read.

    Returns:
        The records as header, sequence and quality text, and the bytes of the
        file read so far, compressed bytes for a gzip file.
    """
    raw = path.open("rb")
    handle = gzip.GzipFile(fileobj=raw) if privacy.file_suffix(path).endswith(".gz") else raw
    records = []
    try:
        while len(records) < limit:
            lines = [handle.readline() for _ in range(4)]
            if not lines[0]:
                break
            records.append((lines[0].decode(errors="replace").rstrip("\r\n"),
                            lines[1].decode(errors="replace").rstrip("\r\n"),
                            lines[3].decode(errors="replace").rstrip("\r\n")))
        used = raw.tell()
    finally:
        handle.close()
        raw.close()
    return records, used


def header_style(header: str) -> str:
    """Name the style of a FASTQ header.

    Args:
        header: The first header line.

    Returns:
        ``illumina``, ``sra`` or ``generic``.
    """
    if ILLUMINA.match(header):
        return "illumina"
    return "sra" if SRA.match(header) else "generic"


def quality_offset(records: list[tuple[str, str, str]]) -> int:
    """Find the ASCII offset of the quality scores.

    Args:
        records: The records.

    Returns:
        64 when no quality character is below ``;``, else 33.
    """
    lowest = min((min(quality) for _, _, quality in records if quality), default="I")
    return 64 if ord(lowest) >= 59 else 33


def read_stats(path: Path) -> dict[str, Any]:
    """Measure one FASTQ file from its first records.

    Args:
        path: The file.

    Returns:
        The record count estimate, the lengths, the quality profile, the header
        style and the index, and the share of N bases.
    """
    records, used = read_records(path, PROFILE_READS)
    if not records:
        return {"reads": 0, "lengths": [], "means": [], "sds": [], "offset": 33, "style": "generic",
                "index": "", "n_share": 0.0, "header": ""}
    offset = quality_offset(records)
    lengths = [len(sequence) for _, sequence, _ in records]
    width = max(lengths)
    scores = np.full((len(records), width), np.nan)
    for row, (_, _, quality) in enumerate(records):
        scores[row, :len(quality)] = np.frombuffer(quality.encode(), dtype=np.uint8) - offset
    size = path.stat().st_size
    estimate = len(records) if len(records) < PROFILE_READS else int(len(records) * size / max(used, 1))
    match = ILLUMINA.match(records[0][0])
    bases = sum(lengths)
    return {"reads": estimate, "lengths": lengths, "means": list(np.nanmean(scores, axis=0)),
            "sds": list(np.nanstd(scores, axis=0)), "offset": offset, "style": header_style(records[0][0]),
            "index": (match["index"] or "") if match else "", "header": records[0][0],
            "n_share": sum(sequence.count("N") for _, sequence, _ in records) / max(bases, 1)}


def profile(paths: list[Path], labels: list[str], options: privacy.Options) -> list[tuple[dict[str, Any], list[str]]]:
    """Profile FASTQ files into one dataset, from the first sample.

    Args:
        paths: The FASTQ files.
        labels: Generic label of each file.
        options: The profile options. ``--set reference=genome.fa`` names a
            public FASTA, and ``--set library=rnaseq`` the library type.

    Returns:
        One dataset config, with the paths of its default fields.
    """
    samples = sample_files(paths)
    first_files = next(iter(samples.values()))
    stats = read_stats(first_files[0])
    paired = all(len(files) == 2 for files in samples.values())
    suffixes = ("_R1", "_R2")
    if paired:
        suffixes = tuple(MATE.match(fastq_stem(path))["suffix"] for path in first_files)
    lengths = stats["lengths"] or [150]
    read_length = max(lengths)
    dataset: dict[str, Any] = {"name": "fastq", "format": "fastq", "file": "fastq", "samples": len(samples),
                               "sample_pattern": most_common_shape(list(samples)), "paired": paired,
                               "pair_suffixes": list(suffixes),
                               "compressed": privacy.file_suffix(first_files[0]).endswith(".gz"),
                               "reads": max(1, min(int(privacy.round_sig(float(stats["reads"]))), MAX_READS)),
                               "read_length": read_length}
    if len(set(lengths)) > 1:
        dataset["length_quantiles"] = [float(round(value)) for value in np.quantile(lengths, np.linspace(0, 1, 11))]
    index = stats["index"]
    dataset.update({"quality_offset": stats["offset"],
                    "quality_means": [round(float(value), 1) for value in stats["means"]],
                    "quality_sds": [round(float(value), 1) for value in stats["sds"]],
                    "header_style": stats["style"], "index_length": len(index.split("+")[0]),
                    "index_dual": "+" in index, "reference": options.extra.get("reference"),
                    "library": options.extra.get("library", "wgs"), "n_share": round(stats["n_share"], 4)})
    defaults = ["insert_mean", "insert_sd"]
    if stats["reads"] > MAX_READS:
        defaults.append("reads")
    return [(dataset, defaults)]


def glimpse(paths: list[Path], labels: list[str], options: privacy.Options) -> list[TableReport]:
    """Report each FASTQ file from its first records, with no sequence.

    Args:
        paths: The FASTQ files.
        labels: Generic label of each file.
        options: The glimpse options.

    Returns:
        One table report per file.
    """
    mates = {path: index + 1 for files in sample_files(paths).values() for index, path in enumerate(files)}
    paired = {path: len(files) == 2 for files in sample_files(paths).values() for path in files}
    reports = []
    for path, label in zip(paths, labels, strict=True):
        stats = read_stats(path)
        lengths = stats["lengths"] or [0]
        means = [value for value in stats["means"] if np.isfinite(value)]
        facts = [Fact("reads", privacy.format_number(float(stats["reads"]))), Fact("paired", str(paired[path]).lower()),
                 Fact("mate", str(mates[path])), Fact("read length", f"{min(lengths)} to {max(lengths)}"),
                 Fact("quality offset", str(stats["offset"])), Fact("header style", stats["style"]),
                 Fact("header shape", repr(privacy.shape(stats["header"]))),
                 Fact("compressed", str(privacy.file_suffix(path).endswith(".gz")).lower()),
                 Fact("n share", privacy.format_number(stats["n_share"]))]
        if means:
            facts.append(Fact("mean quality", f"{privacy.format_number(min(means))} to "
                                              f"{privacy.format_number(max(means))}"))
        reports.append(TableReport(label, "fastq", facts))
    return reports


def audit_values(paths: list[Path], options: privacy.Options) -> dict[str, str]:
    """Collect the header fields and sample names, which become generic tokens.

    Args:
        paths: The FASTQ files.
        options: The options that made the checked text.

    Returns:
        Each sensitive value mapped to its source kind.
    """
    found: dict[str, str] = {sample: "sample name" for sample in sample_files(paths)}
    for path in paths:
        records, _ = read_records(path, 100)
        tokens = Counter(token for header, _, _ in records for token in re.split(r"[\s:/.]+", header.lstrip("@")))
        found.update({token: "header field" for token in tokens if len(token) >= 4 and not token.isdigit()})
    return found


TEMPLATE = '''\
  # A folder of FASTQ files, one file or pair per sample. Reads come from a
  # public reference FASTA, or from a random genome when reference is null.
  - name: {name}
    format: fastq
    file: {name}                      # the output folder
{samples}    paired: true
    pair_suffixes: [_R1_001, _R2_001]
    compressed: true
    reads: 2000                       # reads or pairs per sample
    read_length: 100
    quality_means: [32, 34, 35, 36, 36, 36, 35, 34, 33, 31]  # stretched over the read length
    quality_sds: [2, 2, 2, 2, 2, 3, 3, 3, 4, 5]
    header_style: illumina
    index_length: 8
    reference: null                   # path of a public FASTA, such as one chromosome
    library: wgs                      # wgs samples contigs by length; rnaseq samples transcripts evenly
    insert_mean: 300
    insert_sd: 50
'''


def template(fmt: str, mode: str, name: str) -> tuple[str, str | None]:
    """Return the commented template of a FASTQ dataset.

    Args:
        fmt: ``fastq``.
        mode: ``resample`` or ``model``.
        name: Dataset name.

    Returns:
        The YAML list item. A FASTQ dataset has no feature for an effect.
    """
    samples = "" if mode == "model" else "    samples: 2\n    sample_pattern: SMPL-99\n"
    return TEMPLATE.format(name=name, samples=samples), None


def check_output(dataset: FastqDataset, config: Any, out_dir: Path) -> list[str]:
    """Compare the FASTQ files with the config: one file or pair per sample.

    Args:
        dataset: The dataset config.
        config: The whole config.
        out_dir: The output folder.

    Returns:
        The problems found.
    """
    files = sorted(path for path in (out_dir / dataset.file).iterdir() if path.suffix in {".gz", ".fastq"}) \
        if (out_dir / dataset.file).is_dir() else []
    samples = len(design_rows(config)) if config.mode == "model" else dataset.samples
    expected = samples * (2 if dataset.paired else 1)
    if len(files) != expected:
        return [f"{dataset.file}: {len(files)} FASTQ files, the config gives {expected}"]
    return []
