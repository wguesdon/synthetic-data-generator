"""Tests of the FASTQ format."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest
import yaml

from sdg.audit import find_leaks, sensitive_values
from sdg.compare import compare
from sdg.config import ConfigError, parse_config
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.output import file_sha256
from sdg.privacy import Options, holds_sequence
from sdg.profile import profile, to_yaml
from sdg.report import render
from sdg.templates import init_config

COMPLEMENT = str.maketrans("ACGT", "TGCA")


def generate_data(data: dict, tmp_path: Path, name: str) -> Path:
    """Write a config and generate it.

    Args:
        data: The config as plain data.
        tmp_path: The pytest temporary folder.
        name: Output folder name.

    Returns:
        The output folder.
    """
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    generate(path, tmp_path / name)
    return tmp_path / name


def sequences(path: Path) -> list[str]:
    """Read the sequences of a gzip FASTQ file.

    Args:
        path: The file.

    Returns:
        One sequence per record.
    """
    with gzip.open(path, "rt") as handle:
        return [line.strip() for index, line in enumerate(handle) if index % 4 == 1]


def test_reads_come_from_the_reference(tmp_path: Path) -> None:
    """Mate 1 and mate 2 reads are reference stretches on opposite strands."""
    rng = np.random.default_rng(3)
    reference = "".join(rng.choice(list("ACGT"), size=5000))
    fasta = tmp_path / "ref.fa"
    fasta.write_text(">chr_test\n" + "\n".join(reference[i:i + 60] for i in range(0, 5000, 60)) + "\n")
    data = yaml.safe_load(init_config(["fastq"]))
    data["datasets"][0].update({"reference": str(fasta), "reads": 300, "quality_means": [40], "quality_sds": [0]})
    folder = generate_data(data, tmp_path, "out") / "dataset_1"
    both = reference + "|" + reference.translate(COMPLEMENT)[::-1]
    first, second = sequences(folder / "SMPL-01_R1_001.fastq.gz"), sequences(folder / "SMPL-01_R2_001.fastq.gz")
    assert np.mean([read in both for read in first]) > 0.95
    assert np.mean([read in both for read in second]) > 0.95
    pairs = [(read in reference) != (mate in reference) for read, mate in zip(first, second, strict=True)]
    assert np.mean(pairs) > 0.95


def test_reruns_give_the_same_bytes(tmp_path: Path) -> None:
    """The gzip time is fixed, so two runs write the same files."""
    data = yaml.safe_load(init_config(["fastq"]))
    one = generate_data(data, tmp_path, "one") / "dataset_1" / "SMPL-02_R2_001.fastq.gz"
    two = generate_data(data, tmp_path, "two") / "dataset_1" / "SMPL-02_R2_001.fastq.gz"
    assert file_sha256(one) == file_sha256(two)


def test_round_trip(tmp_path: Path) -> None:
    """Glimpse, profile, generate and glimpse again: no structure error."""
    first = generate_data(yaml.safe_load(init_config(["fastq"])), tmp_path, "first") / "dataset_1"
    report = glimpse([first], Options())
    result = profile([first], Options())
    dataset = result.config["datasets"][0]
    assert dataset["paired"] and dataset["pair_suffixes"] == ["_R1_001", "_R2_001"]
    assert dataset["read_length"] == 100 and dataset["reads"] == 2000
    second = generate_data(yaml.safe_load(to_yaml(result)), tmp_path, "second") / "fastq"
    assert [item for item in compare(report, glimpse([second], Options())) if item.level == "error"] == []


def test_model_mode_uses_the_design_samples(tmp_path: Path) -> None:
    """In the model mode, each design sample gets one pair."""
    data = yaml.safe_load(init_config(["fastq"], "model"))
    data["effects"] = []
    data["datasets"][0]["reads"] = 10
    folder = generate_data(data, tmp_path, "out") / "dataset_1"
    assert len(list(folder.glob("*_R1_001.fastq.gz"))) == 40
    assert (folder / "SUBJ-001_baseline_R1_001.fastq.gz").is_file()


def test_real_headers_and_sequences_stay_private(tmp_path: Path) -> None:
    """No header field, sample name or sequence reaches a report or a config."""
    folder = tmp_path / "real"
    folder.mkdir()
    rng = np.random.default_rng(5)
    for mate in (1, 2):
        records = []
        for index in range(50):
            read = "".join(rng.choice(list("ACGT"), size=75))
            records.append(f"@SIM042:7:FLOWCELL9:2:1101:{1000 + index}:2000 {mate}:N:0:GATTACAG\n{read}\n+\n"
                           + "F" * 75 + "\n")
        with gzip.open(folder / f"PATIENT07_S3_L002_R{mate}_001.fastq.gz", "wt") as handle:
            handle.write("".join(records))
    paths = sorted(folder.glob("*.fastq.gz"))
    text = render(glimpse(paths, Options()))
    config = to_yaml(profile(paths, Options()))
    for secret in ("SIM042", "FLOWCELL9", "PATIENT07", "GATTACAG"):
        assert secret not in text and secret not in config
    assert not holds_sequence(text) and not holds_sequence(config)
    values = sensitive_values(paths, Options())
    assert find_leaks(text, values) == [] and find_leaks(config, values) == []
    assert find_leaks("instrument: FLOWCELL9\n", values)[0].kind == "header field"


def test_missing_reference_is_a_config_error() -> None:
    """A reference path that does not exist fails at the check."""
    data = yaml.safe_load(init_config(["fastq"]))
    data["datasets"][0]["reference"] = "/no/such/genome.fa"
    with pytest.raises(ConfigError, match="no FASTA file"):
        parse_config(data)
