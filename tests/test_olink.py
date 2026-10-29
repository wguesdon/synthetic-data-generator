"""Tests of the Olink format in the long layout."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from sdg.audit import find_leaks, sensitive_values
from sdg.compare import compare
from sdg.formats import detect_module, olink, tabular
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import fact_map, render
from sdg.templates import init_config


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


@pytest.mark.parametrize("suffix", [".csv", ".xlsx", ".parquet"])
def test_round_trip(suffix: str, tmp_path: Path) -> None:
    """Glimpse, profile, generate and glimpse again: no structure error, in each file form."""
    data = yaml.safe_load(init_config(["olink"]))
    data["datasets"][0]["file"] = f"dataset_1{suffix}"
    first = generate_data(data, tmp_path, "first") / f"dataset_1{suffix}"
    assert detect_module(first) is olink
    report = glimpse([first], Options())
    assert report.tables[0].format == "olink" and fact_map(report.tables[0].facts)["assays"] == "2"
    result = profile([first], Options())
    fitted = result.config["datasets"][0]
    assert [assay["assay"] for assay in fitted["assays"]] == ["IL6", "TNF"]
    assert fitted["samples"] == 40 and fitted["scale"] == "log2"
    second = generate_data(yaml.safe_load(to_yaml(result)), tmp_path, "second") / f"file_1{suffix}"
    assert [item for item in compare(report, glimpse([second], Options())) if item.level == "error"] == []


def test_model_effect_on_npx(tmp_path: Path) -> None:
    """A fold change of 2 adds 1 NPX, because NPX is on the log2 scale."""
    data = yaml.safe_load(init_config(["olink"], "model"))
    data["design"]["subjects"]["groups"] = {"group_a": 60, "group_b": 60}
    data["design"]["timepoints"] = ["baseline"]
    frame = pd.read_csv(generate_data(data, tmp_path, "out") / "dataset_1.csv", sep=";")
    il6 = frame[frame["Assay"] == "IL6"].reset_index(drop=True)
    shift = il6["NPX"][:60].mean() - il6["NPX"][60:].mean()
    assert shift == pytest.approx(1.0, abs=0.35)
    assert il6["SampleID"][0] == "SUBJ-001_baseline"


def test_plain_table_is_not_olink(tmp_path: Path) -> None:
    """A table without NPX columns goes to the tabular module."""
    path = tmp_path / "plain.csv"
    path.write_text("SampleID,Value\nS1,1\n", encoding="utf-8")
    assert detect_module(path) is tabular


def test_quant_column_is_linear(tmp_path: Path) -> None:
    """A Quant value column profiles on the linear scale."""
    rows = ["SampleID;Assay;Quant"] + [f"S{index:02d};IL6;{1.5 + index:.2f}" for index in range(12)]
    path = tmp_path / "target48.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dataset = profile([path], Options()).config["datasets"][0]
    assert dataset["scale"] == "linear" and dataset["decimals"] == 2


def test_sample_ids_stay_private(tmp_path: Path) -> None:
    """Sample identifiers print as shapes, and the audit knows them."""
    rows = ["SampleID;Assay;NPX;PlateID"] + [f"DONOR{index:03d};IL6;{index / 7:.3f};RUN0042"
                                             for index in range(15)]
    path = tmp_path / "npx.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    text = render(glimpse([path], Options()))
    assert "DONOR" not in text and "RUN0042" not in text
    config_text = to_yaml(profile([path], Options()))
    values = sensitive_values([path], Options())
    assert find_leaks(text, values) == [] and find_leaks(config_text, values) == []
    assert find_leaks("plate: RUN0042\n", values)[0].kind == "plate id"


def test_generic_labels_hide_the_sheet_name(tmp_path: Path) -> None:
    """With generic labels, the sheet name of a long workbook leaves the report and the config."""
    data = yaml.safe_load(init_config(["olink"]))
    data["datasets"][0].update({"file": "dataset_1.xlsx", "sheet": "Trial Results"})
    first = generate_data(data, tmp_path, "first") / "dataset_1.xlsx"
    options = Options(generic_labels=True)
    assert "Trial Results" not in render(glimpse([first], options))
    assert "Trial Results" not in to_yaml(profile([first], options))
