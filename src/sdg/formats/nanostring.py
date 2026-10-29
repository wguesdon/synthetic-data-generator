"""NanoString nCounter RCC files: a folder with one file per sample.

An RCC file has a ``Header``, ``Sample_Attributes``, ``Lane_Attributes``,
``Code_Summary`` and ``Messages`` section. The code set, the code names and
the accessions are public, so they stay unless ``--generic-labels`` is set. The
sample and lane attributes become generic tokens (privacy rule 8), except the
numeric lane attributes, such as the binding density, which keep rounded
quantiles across files.

Counts follow a lane factor, which stands for the binding density and the
positive control scaling, and the mean of each code. In the model mode, each
design sample is one file and an effect multiplies the mean of an endogenous or
housekeeping code.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import Field, field_validator, model_validator

from sdg import privacy
from sdg.config_base import DatasetBase, StrictModel
from sdg.formats import tabular
from sdg.matrix import most_common_shape, sample_names, strip_replicate
from sdg.modes.model import Feature, lognormal_quantiles, replicate_count, sample_count, simulate
from sdg.modes.resample import invented_identifiers, quantile_draws
from sdg.report import ColumnReport, Fact, TableReport, counted
from sdg.seeding import stream
from sdg.tables import analyze_column, column_report, read_text

if TYPE_CHECKING:
    from sdg.generate import Context

FORMATS = ("nanostring",)
SUFFIXES = (".rcc",)

#: Code classes that carry biology, so effects can target them.
BIOLOGICAL_CLASSES = {"Endogenous", "Housekeeping"}

#: A section line, such as ``<Header>`` or ``</Header>``.
SECTION = re.compile(r"^<(/?)(\w+)>$")

#: Sample attributes that hold public names of the platform, kept by default.
PUBLIC_ATTRIBUTES = {"GeneRLF", "SystemAPF", "AssayType"}

#: Sample attributes that the writer always fills. Another attribute, such as
#: ``Owner``, is empty unless the config lists it in ``text_attributes``.
WRITTEN_ATTRIBUTES = {"ID", "Date", *PUBLIC_ATTRIBUTES}

#: Defaults where the privacy rules forbid a measurement.
DEFAULT_MEANS = {"Endogenous": 100.0, "Housekeeping": 1000.0, "Positive": 1000.0, "Negative": 10.0}
DEFAULT_LANE = {"FovCount": [280.0, 280.0], "FovCounted": [260.0, 280.0], "StagePosition": [1.0, 6.0],
                "BindingDensity": [0.1, 2.0]}

#: Lanes of one nCounter cartridge.
LANES = 12


class RccCode(StrictModel):
    """One code of the code set.

    Attributes:
        code_class: Class, such as ``Endogenous``, ``Housekeeping``,
            ``Positive`` or ``Negative``.
        name: Code name, such as a gene symbol or ``POS_A(128)``.
        accession: Accession, such as ``NM_000600.1``.
        mean: Mean count across samples.
    """

    code_class: str
    name: str
    accession: str | None = None
    mean: float = Field(ge=0.0)


class LaneAttribute(StrictModel):
    """One lane attribute.

    Attributes:
        name: Attribute name, such as ``BindingDensity``.
        quantiles: Quantiles across files of a numeric attribute. ``None`` writes
            a generic text, and the lane number for ``ID``.
        decimals: Decimal places of a numeric attribute.
        empty: The attribute is empty in each file, as ``CartridgeBarcode``
            can be.
    """

    name: str
    quantiles: list[float] | None = None
    decimals: int = Field(default=0, ge=0, le=6)
    empty: bool = False

    @model_validator(mode="after")
    def check_empty(self) -> LaneAttribute:
        """Check that an empty attribute has no quantiles.

        Returns:
            The unchanged attribute.

        Raises:
            ValueError: If an empty attribute has quantiles.
        """
        if self.empty and self.quantiles is not None:
            raise ValueError(f"{self.name}: an empty attribute has no quantiles")
        return self

    @field_validator("quantiles")
    @classmethod
    def check_order(cls, values: list[float] | None) -> list[float] | None:
        """Check that the quantiles never decrease.

        Args:
            values: The quantiles, or None.

        Returns:
            The unchanged quantiles.
        """
        return values if values is None else tabular.check_non_decreasing(values)


def default_header() -> dict[str, str]:
    """Return the header of an RCC file from a recent nCounter software.

    Returns:
        The file version and the software version.
    """
    return {"FileVersion": "1.7", "SoftwareVersion": "4.0.0.3"}


def default_sample_attributes() -> list[str]:
    """Return the sample attribute names of an RCC file.

    Returns:
        The names in instrument order.
    """
    return ["ID", "Owner", "Comments", "Date", "GeneRLF", "SystemAPF"]


class NanostringDataset(DatasetBase):
    """A folder of nCounter RCC files.

    Attributes:
        format: ``nanostring``.
        file: Output folder.
        samples: Number of files in the resample mode.
        sample_pattern: Shape of a sample identifier, which is also the file name.
        header: Header fields and their values, such as the file version.
        sample_attributes: Sample attribute names in order.
        text_attributes: Sample attributes, such as ``Owner``, that hold a
            generic text. ``ID``, ``Date``, ``GeneRLF``, ``SystemAPF`` and
            ``AssayType`` are always written; the other attributes are empty.
        gene_rlf: Code set name.
        system_apf: System name.
        assay_type: Assay type, or None.
        lane_attributes: Lane attributes in order.
        codes: The codes in delivered order.
        count_cv: Coefficient of variation of a code mean between samples.
        lane_cv: Coefficient of variation of the lane factor.
        line_ending: ``crlf``, as the instrument writes, or ``lf``.
    """

    format: Literal["nanostring"]
    samples: int | None = Field(default=None, ge=1)
    sample_pattern: str = "SMPL-99"
    header: dict[str, str] = Field(default_factory=default_header)
    sample_attributes: list[str] = Field(default_factory=default_sample_attributes)
    text_attributes: list[str] = Field(default_factory=list)
    gene_rlf: str = "NS_CODESET"
    system_apf: str = "n6_vDV1"
    assay_type: str | None = None
    lane_attributes: list[LaneAttribute] = Field(default_factory=list)
    codes: list[RccCode] = Field(min_length=1)
    count_cv: float = Field(default=0.4, ge=0.0)
    lane_cv: float = Field(default=0.2, ge=0.0)
    line_ending: Literal["crlf", "lf"] = "crlf"

    @model_validator(mode="after")
    def check_text_attributes(self) -> NanostringDataset:
        """Check that each text attribute is a sample attribute that the writer leaves empty.

        Returns:
            The unchanged dataset.

        Raises:
            ValueError: If a text attribute is not in ``sample_attributes`` or is
                always written.
        """
        for name in self.text_attributes:
            if name not in self.sample_attributes or name in WRITTEN_ATTRIBUTES:
                raise ValueError(f"text_attributes: {name!r} must be a sample attribute other than "
                                 f"{', '.join(sorted(WRITTEN_ATTRIBUTES))}")
        return self

    def feature_names(self) -> set[str]:
        """Return the endogenous and housekeeping codes, which effects can target.

        Returns:
            The code names.
        """
        return {code.name for code in self.codes if code.code_class in BIOLOGICAL_CLASSES}

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


DATASET_MODELS = (NanostringDataset,)


def parse_rcc(text: str) -> dict[str, Any]:
    """Parse the sections of an RCC file.

    Args:
        text: The file text.

    Returns:
        ``Header``, ``Sample_Attributes`` and ``Lane_Attributes`` as ordered
        mappings, ``Code_Summary`` as a list of rows, and the line ending.
    """
    sections: dict[str, Any] = {"line_ending": "crlf" if "\r\n" in text else "lf"}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if match := SECTION.match(line):
            current = None if match[1] else match[2]
            if current:
                sections[current] = [] if current in {"Code_Summary", "Messages"} else {}
            continue
        if not line or current is None:
            continue
        if current == "Code_Summary":
            sections[current].append(line.split(","))
        elif current != "Messages":
            key, _, value = line.partition(",")
            sections[current][key] = value
    return sections


def read_rcc(path: Path) -> dict[str, Any]:
    """Read and parse one RCC file.

    Args:
        path: The file.

    Returns:
        The parsed sections.
    """
    return parse_rcc(read_text(path))


def code_rows(sections: dict[str, Any]) -> list[dict[str, str]]:
    """Return the code summary of a parsed file as dictionaries.

    Args:
        sections: The parsed sections.

    Returns:
        One dictionary per code, keyed by the header of the code summary.
    """
    rows = sections.get("Code_Summary", [])
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row, strict=False)) for row in rows[1:]]


def expected_counts(dataset: NanostringDataset, context: Context) -> tuple[list[str], np.ndarray]:
    """Return the sample names and the expected count of each code, samples by codes.

    Args:
        dataset: The dataset.
        context: The generation context, which collects the truth rows.

    Returns:
        The sample names and the expected counts.
    """
    config = context.config
    means = np.array([code.mean for code in dataset.codes])
    biological = np.array([code.code_class in BIOLOGICAL_CLASSES for code in dataset.codes])
    if config.mode == "model":
        features = [Feature(code.name, lognormal_quantiles(code.mean, dataset.count_cv))
                    for code, keep in zip(dataset.codes, biological, strict=True) if keep]
        simulation = simulate(config, dataset.name, features)
        context.add_truth("effects", simulation.effects)
        samples = sample_names([(row.sample.sample_id, row.replicate) for row in simulation.rows],
                               replicate_count(config, dataset.name))
        expected = np.tile(means, (len(samples), 1))
        expected[:, biological] = simulation.values
    else:
        samples = invented_identifiers(dataset.sample_pattern, dataset.samples,
                                       stream(config.seed, dataset.name, "samples"))
        sigma = math.sqrt(math.log(1.0 + dataset.count_cv ** 2))
        noise = stream(config.seed, dataset.name, "biological").standard_normal((len(samples), len(means)))
        expected = means[None, :] * np.where(biological[None, :], np.exp(sigma * noise - sigma ** 2 / 2.0), 1.0)
    lane_sigma = math.sqrt(math.log(1.0 + dataset.lane_cv ** 2))
    lanes = np.exp(lane_sigma * stream(config.seed, dataset.name, "lanes").standard_normal(len(samples)))
    negative = np.array([code.code_class == "Negative" for code in dataset.codes])
    return samples, expected * np.where(negative[None, :], 1.0, lanes[:, None])


def lane_value(attribute: LaneAttribute, lane: int, sample_index: int, rng: np.random.Generator) -> str:
    """Write one lane attribute of one file.

    Args:
        attribute: The attribute.
        lane: Lane number.
        sample_index: Position of the sample, from 0.
        rng: Random stream of the lane attributes.

    Returns:
        The value text.
    """
    if attribute.name == "ID":
        return str(lane)
    if attribute.empty:
        return ""
    if attribute.quantiles is None:
        return f"{attribute.name}_{sample_index // LANES + 1}"
    value = float(quantile_draws(attribute.quantiles, rng.random(1))[0])
    return f"{value:.{attribute.decimals}f}"


def rcc_text(dataset: NanostringDataset, sample: str, sample_index: int, counts: np.ndarray,
             rng: np.random.Generator) -> str:
    """Write the text of one RCC file.

    Args:
        dataset: The dataset.
        sample: Sample identifier.
        sample_index: Position of the sample, from 0.
        counts: Count of each code.
        rng: Random stream of the lane attributes.

    Returns:
        The file text with the configured line ending.
    """
    date = (dt.date(2000, 1, 1) + dt.timedelta(days=sample_index // LANES)).strftime("%Y%m%d")
    attributes = {"ID": sample, "Date": date, "GeneRLF": dataset.gene_rlf, "SystemAPF": dataset.system_apf,
                  "AssayType": dataset.assay_type or ""}
    attributes.update({name: f"{name}_1" for name in dataset.text_attributes})
    lane = sample_index % LANES + 1
    lines = ["<Header>", *[f"{key},{value}" for key, value in dataset.header.items()], "</Header>", "",
             "<Sample_Attributes>", *[f"{key},{attributes.get(key, '')}" for key in dataset.sample_attributes],
             "</Sample_Attributes>", "", "<Lane_Attributes>",
             *[f"{item.name},{lane_value(item, lane, sample_index, rng)}" for item in dataset.lane_attributes],
             "</Lane_Attributes>", "", "<Code_Summary>", "CodeClass,Name,Accession,Count",
             *[f"{code.code_class},{code.name},{code.accession or ''},{int(count)}"
               for code, count in zip(dataset.codes, counts, strict=True)],
             "</Code_Summary>", "", "<Messages>", "</Messages>"]
    newline = "\r\n" if dataset.line_ending == "crlf" else "\n"
    return newline.join(lines) + newline


def generate(dataset: NanostringDataset, context: Context) -> list[Path]:
    """Write one RCC file per sample.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The files written.
    """
    seed = context.config.seed
    samples, expected = expected_counts(dataset, context)
    counts = stream(seed, dataset.name, "counts").poisson(expected)
    folder = context.out_dir / dataset.file
    folder.mkdir(parents=True, exist_ok=True)
    rng = stream(seed, dataset.name, "lane_attributes")
    paths = []
    for index, sample in enumerate(samples):
        path = folder / f"{sample}.RCC"
        path.write_bytes(rcc_text(dataset, sample, index, counts[index], rng).encode("utf-8"))
        paths.append(path)
    return paths


def counts_by_code(parsed: list[dict[str, Any]]) -> tuple[list[dict[str, str]], np.ndarray]:
    """Return the codes of the first file and the counts of every file.

    Args:
        parsed: The parsed files.

    Returns:
        The code rows of the first file and the counts, files by codes.
    """
    codes = code_rows(parsed[0])
    names = [code["Name"] for code in codes]
    matrix = np.zeros((len(parsed), len(codes)))
    for index, sections in enumerate(parsed):
        found = {row["Name"]: row.get("Count", "0") for row in code_rows(sections)}
        matrix[index] = [float(found.get(name, 0) or 0) for name in names]
    return codes, matrix


def lane_config(parsed: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Profile the lane attributes: quantiles of the numeric ones, names of the others.

    Args:
        parsed: The parsed files.

    Returns:
        The lane attribute configs and the paths of their default fields.
    """
    attributes, defaults = [], []
    for index, name in enumerate(parsed[0].get("Lane_Attributes", {})):
        values = [sections.get("Lane_Attributes", {}).get(name, "") for sections in parsed]
        numbers = [float(value) for value in values if re.fullmatch(r"-?\d+(\.\d+)?", value or "")]
        attribute: dict[str, Any] = {"name": name}
        if name != "ID" and not any(values):
            attribute["empty"] = True
        elif name != "ID" and numbers and len(numbers) == len(values):
            places = max(len(value.split(".", 1)[1]) if "." in value else 0 for value in values)
            if len(numbers) >= privacy.MIN_VALUES:
                attribute["quantiles"] = tabular.rounded_quantiles(numbers)
            else:
                attribute["quantiles"] = DEFAULT_LANE.get(name, [0.0, 1.0])
                defaults.append(f"lane_attributes[{index}].quantiles")
            attribute["decimals"] = places
        attributes.append(attribute)
    return attributes, defaults


def profile(paths: list[Path], labels: list[str], options: privacy.Options) -> list[tuple[dict[str, Any], list[str]]]:
    """Profile a set of RCC files into one dataset.

    Args:
        paths: The RCC files.
        labels: Generic label of each file.
        options: The profile options.

    Returns:
        One dataset config, with the paths of its default fields.
    """
    parsed = [read_rcc(path) for path in paths]
    first = parsed[0]
    codes, counts = counts_by_code(parsed)
    enough = len(parsed) >= privacy.MIN_VALUES
    defaults = []
    code_configs = []
    for index, code in enumerate(codes):
        name = f"code_{index + 1}" if options.generic_labels and code["CodeClass"] in BIOLOGICAL_CLASSES \
            else code["Name"]
        item: dict[str, Any] = {"code_class": code["CodeClass"], "name": name}
        if code.get("Accession") and not options.generic_labels:
            item["accession"] = code["Accession"]
        item["mean"] = privacy.round_sig(float(counts[:, index].mean())) if enough \
            else DEFAULT_MEANS.get(code["CodeClass"], 100.0)
        code_configs.append(item)
    if not enough:
        defaults.append("codes[*].mean")
    sample_ids = [sections.get("Sample_Attributes", {}).get("ID", path.stem) for sections, path in
                  zip(parsed, paths, strict=True)]
    attributes = first.get("Sample_Attributes", {})
    lanes, lane_defaults = lane_config(parsed)
    biological = [index for index, code in enumerate(codes) if code["CodeClass"] in BIOLOGICAL_CLASSES]
    positive = [index for index, code in enumerate(codes) if code["CodeClass"] == "Positive"]
    dataset: dict[str, Any] = {"name": "rcc", "format": "nanostring", "file": "rcc", "samples": len(parsed),
                               "sample_pattern": most_common_shape(sample_ids), "header": dict(first.get("Header", {})),
                               "sample_attributes": list(attributes),
                               "text_attributes": [name for name in attributes if name not in WRITTEN_ATTRIBUTES
                                                   and any(sections.get("Sample_Attributes", {}).get(name)
                                                           for sections in parsed)],
                               "gene_rlf": "CODESET" if options.generic_labels else attributes.get("GeneRLF", ""),
                               "system_apf": attributes.get("SystemAPF", "n6_vDV1"),
                               "assay_type": attributes.get("AssayType"), "lane_attributes": lanes,
                               "codes": code_configs, "line_ending": first["line_ending"]}
    if enough and biological:
        with np.errstate(divide="ignore", invalid="ignore"):
            cvs = counts[:, biological].std(axis=0, ddof=1) / counts[:, biological].mean(axis=0)
        dataset["count_cv"] = privacy.round_sig(float(np.nanmedian(cvs)))
    if enough and positive:
        sums = counts[:, positive].sum(axis=1)
        dataset["lane_cv"] = privacy.round_sig(float(sums.std(ddof=1) / sums.mean()))
    return [(dataset, defaults + lane_defaults)]


def attribute_columns(parsed: list[dict[str, Any]], section: str, options: privacy.Options) -> list[ColumnReport]:
    """Report each attribute of a section across files, as a column.

    Args:
        parsed: The parsed files.
        section: ``Sample_Attributes`` or ``Lane_Attributes``.
        options: The glimpse options.

    Returns:
        One column report per attribute. A ``Date`` reads as a date.
    """
    reports = []
    for name in parsed[0].get(section, {}):
        cells: list[object] = [sections.get(section, {}).get(name) or None for sections in parsed]
        if name == "Date":
            cells = [dt.datetime.strptime(cell, "%Y%m%d") if isinstance(cell, str) and re.fullmatch(r"\d{8}", cell)
                     else cell for cell in cells]
        stats = analyze_column(name, cells, options.max_categories)
        if stats.kind == "date":
            stats.date_format = "%Y%m%d"
        public = name in PUBLIC_ATTRIBUTES and not options.generic_labels
        # A numeric attribute can hold a text token, such as an operator name
        # in a CartridgeID, which rule 8 keeps out like any other text value.
        if (stats.kind == "categorical" or (stats.kind == "numeric" and stats.tokens)) and not public:
            stats.kind = "text"
        report = column_report(stats, False, options.max_categories)
        report.name = f"{section}.{name}"
        reports.append(report)
    return reports


def glimpse(paths: list[Path], labels: list[str], options: privacy.Options) -> list[TableReport]:
    """Report a set of RCC files as one table.

    Args:
        paths: The RCC files.
        labels: Generic label of each file.
        options: The glimpse options.

    Returns:
        One table report, labelled with the first and last file.
    """
    parsed = [read_rcc(path) for path in paths]
    codes, counts = counts_by_code(parsed)
    classes = Counter(code["CodeClass"] for code in codes)
    attributes = parsed[0].get("Sample_Attributes", {})
    facts = [Fact("rows", str(len(parsed))), Fact("codes", str(len(codes))),
             Fact("code classes", counted(classes, 20)),
             Fact("header", ", ".join(f"{key}={value}" for key, value in parsed[0].get("Header", {}).items())),
             Fact("line ending", parsed[0]["line_ending"])]
    if not options.generic_labels and attributes.get("GeneRLF"):
        facts.append(Fact("code set", repr(attributes["GeneRLF"])))
    if len(parsed) >= privacy.MIN_VALUES and counts.size:
        facts.append(Fact("count range", f"{privacy.format_number(float(counts.min()))} to "
                                         f"{privacy.format_number(float(counts.max()))}"))
    label = labels[0] if len(labels) == 1 else f"{labels[0]} to {labels[-1]}"
    report = TableReport(label, "nanostring", facts)
    report.columns = (attribute_columns(parsed, "Sample_Attributes", options)
                      + attribute_columns(parsed, "Lane_Attributes", options))
    return [report]


def audit_values(paths: list[Path], options: privacy.Options) -> dict[str, str]:
    """Collect the sample and lane attribute values, which become generic tokens.

    Args:
        paths: The RCC files.
        options: The options that made the checked text.

    Returns:
        Each sensitive value mapped to its source kind.
    """
    found: dict[str, str] = {}
    for path in paths:
        sections = read_rcc(path)
        for key, value in sections.get("Sample_Attributes", {}).items():
            if value and key not in PUBLIC_ATTRIBUTES:
                found[value] = f"sample attribute {key}"
        for key, value in sections.get("Lane_Attributes", {}).items():
            if value and not re.fullmatch(r"-?\d+(\.\d+)?", value):
                found[value] = f"lane attribute {key}"
    return found


TEMPLATE = '''\
  # A folder of nCounter RCC files, one per sample. The model mode takes the
  # samples of the design; the resample mode needs samples.
  - name: {name}
    format: nanostring
    file: {name}                      # the output folder
{samples}    gene_rlf: NS_CODESET              # code set name
    lane_attributes:
      - {{name: ID}}                    # the lane number
      - {{name: FovCount, quantiles: [280, 280]}}
      - {{name: FovCounted, quantiles: [260, 280]}}
      - {{name: BindingDensity, quantiles: [0.1, 2.0], decimals: 2}}
    codes:                            # code set in order; mean count across samples
      - {{code_class: Endogenous, name: IL6, accession: NM_000600.1, mean: 150}}
      - {{code_class: Endogenous, name: TNF, accession: NM_000594.2, mean: 80}}
      - {{code_class: Housekeeping, name: GAPDH, accession: NM_002046.3, mean: 3000}}
      - {{code_class: Positive, name: POS_A(128), accession: ERCC_00117.1, mean: 30000}}
      - {{code_class: Negative, name: NEG_A(0), accession: ERCC_00096.1, mean: 10}}
    count_cv: 0.4                     # spread of an endogenous mean between samples
    lane_cv: 0.2                      # spread of the lane factor
    line_ending: crlf
'''


def template(fmt: str, mode: str, name: str) -> tuple[str, str | None]:
    """Return the commented template of a NanoString dataset.

    Args:
        fmt: ``nanostring``.
        mode: ``resample`` or ``model``.
        name: Dataset name.

    Returns:
        The YAML list item, and the feature that a template effect can target.
    """
    samples = "" if mode == "model" else "    samples: 12\n    sample_pattern: SMPL-99\n"
    return TEMPLATE.format(name=name, samples=samples), "IL6" if mode == "model" else None


def check_output(dataset: NanostringDataset, config: Any, out_dir: Path) -> list[str]:
    """Compare the RCC files with the config: file count and code set.

    Args:
        dataset: The dataset config.
        config: The whole config.
        out_dir: The output folder.

    Returns:
        The problems found.
    """
    files = sorted((out_dir / dataset.file).glob("*.RCC"))
    expected = sample_count(dataset, config)
    issues = []
    if len(files) != expected:
        issues.append(f"{dataset.file}: {len(files)} RCC files, the config gives {expected}")
    if files and [row["Name"] for row in code_rows(read_rcc(files[0]))] != [code.name for code in dataset.codes]:
        issues.append(f"{dataset.file}: the codes of {files[0].name} differ from the config")
    return issues


def observed(dataset: NanostringDataset, out_dir: Path) -> dict[tuple[str, str], list[float]]:
    """Read the counts back, scaled to the positive controls, by sample and code.

    Args:
        dataset: The dataset config.
        out_dir: The output folder.

    Returns:
        The scaled counts of each sample and code.
    """
    files = sorted((out_dir / dataset.file).glob("*.RCC"))
    parsed = [code_rows(read_rcc(path)) for path in files]
    positives = [sum(float(row["Count"]) for row in rows if row["CodeClass"] == "Positive") for rows in parsed]
    scale = float(np.mean(positives)) if positives and np.mean(positives) > 0 else 1.0
    found: dict[tuple[str, str], list[float]] = {}
    for path, rows, positive in zip(files, parsed, positives, strict=True):
        factor = scale / positive if positive > 0 else 1.0
        for row in rows:
            found.setdefault((strip_replicate(path.stem), row["Name"]), []).append(float(row["Count"]) * factor)
    return found
