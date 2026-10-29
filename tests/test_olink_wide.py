"""Tests of the Olink format in the wide layout of NPX Manager."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
import yaml

from sdg.audit import find_leaks, sensitive_values
from sdg.compare import compare
from sdg.config import ConfigError, parse_config
from sdg.formats import detect_module, olink
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import fact_map, render
from sdg.templates import init_config
from sdg.validate import validate

#: Values of the invented real file that no report or config may hold.
PROJECT = "ACME_trial_7"
DONOR = "DONOR"
RUN = "RUN0042_LAB.csv"


def wide_config(mode: str = "resample", suffix: str = ".csv") -> dict[str, Any]:
    """Return the Olink template turned into the wide layout.

    The panel loses "Target 96" from its name, because a Target 96 panel needs
    its 92 assays and the template has two.

    Args:
        mode: ``resample`` or ``model``.
        suffix: Suffix of the output file.

    Returns:
        The config as plain data.
    """
    data = yaml.safe_load(init_config(["olink"], mode))
    dataset = data["datasets"][0]
    del dataset["columns"]
    dataset.update({"layout": "wide", "file": f"dataset_1{suffix}"})
    for assay in dataset["assays"]:
        assay["panel"] = "Olink Inflammation"
    return data


def generate_data(data: dict[str, Any], tmp_path: Path, name: str) -> Path:
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


def read_rows(path: Path) -> list[list[str]]:
    """Read a wide CSV file as text rows.

    Args:
        path: The file.

    Returns:
        The rows.
    """
    with path.open(encoding="utf-8") as handle:
        return list(csv.reader(handle, delimiter=";"))


def write_real_file(path: Path) -> Path:
    """Write an invented wide file with a project name, donors and run files.

    Args:
        path: The output file.

    Returns:
        The file.
    """
    assays = [("IL6", "P05231", "OID00001"), ("TNF", "P01375", "OID00002"), ("CCL2", "P13500", "OID00003")]
    panel = "Olink Inflammation"
    rows = [[PROJECT, "NPX Manager 2.1.0", "", "", "", ""], ["NPX Data", "", "", "", "", ""],
            ["Panel", panel, panel, panel, panel, panel],
            ["Assay", *[assay for assay, _, _ in assays], "Plate ID", "QC Warning"],
            ["Uniprot ID", *[uniprot for _, uniprot, _ in assays], "", ""],
            ["OlinkID", *[oid for _, _, oid in assays], "", ""], [""] * 6]
    for index in range(30):
        values = [f"{2 + (index * 7 + offset * 3) % 11 / 3:.4f}" for offset in range(3)]
        rows.append([f"{DONOR}{index:03d}", *values, RUN, "Warning" if index in (4, 17) else "Pass"])
    rows += [[""] * 6, ["LOD", "1.25", "0.5", "2.1", "", ""], ["Missing Data freq.", "0.1", "0", "0.25", "", ""],
             ["Normalization", "Intensity", "Intensity", "Intensity", "", ""]]
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, delimiter=";", lineterminator="\n").writerows(rows)
    return path


def test_wide_file_layout(tmp_path: Path) -> None:
    """The file has the head, top, sample and bottom rows of NPX Manager, split by empty rows."""
    path = generate_data(wide_config(), tmp_path, "out") / "dataset_1.csv"
    rows = read_rows(path)
    assert rows[0][0] == "project_1" and rows[1][0] == "NPX Data"
    assert [row[0] for row in rows[2:6]] == ["Panel", "Assay", "Uniprot ID", "OlinkID"]
    assert rows[3][1:] == ["IL6", "TNF", "Plate ID", "QC Warning"]
    assert not any(rows[6]) and not any(rows[47])
    assert len(rows[7:47]) == 40 and [row[0] for row in rows[48:]] == ["LOD", "Missing Data freq."]
    assert len({len(row) for row in rows}) == 1
    assert detect_module(path) is olink and olink.is_wide(path)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_wide_round_trip(suffix: str, tmp_path: Path) -> None:
    """Glimpse, profile, generate and glimpse again: no structure error."""
    first = generate_data(wide_config(suffix=suffix), tmp_path, "first") / f"dataset_1{suffix}"
    report = glimpse([first], Options())
    facts = fact_map(report.tables[0].facts)
    assert facts["layout"] == "wide" and facts["assays"] == "2" and facts["samples"] == "40"
    result = profile([first], Options())
    fitted = result.config["datasets"][0]
    assert fitted["layout"] == "wide" and [assay["assay"] for assay in fitted["assays"]] == ["IL6", "TNF"]
    assert [column["role"] for column in fitted["wide"]["columns"]] == ["plate_id", "sample_qc"]
    second = generate_data(yaml.safe_load(to_yaml(result)), tmp_path, "second") / f"file_1{suffix}"
    assert [item for item in compare(report, glimpse([second], Options())) if item.level == "error"] == []


def test_wide_real_file_stays_private(tmp_path: Path) -> None:
    """Donors, run files and the project name never reach a report or a config."""
    path = write_real_file(tmp_path / "npx_wide.csv")
    text = render(glimpse([path], Options()))
    result = profile([path], Options())
    config_text = to_yaml(result)
    for secret in (PROJECT, DONOR, RUN):
        assert secret not in text and secret not in config_text
    values = sensitive_values([path], Options())
    assert values[PROJECT] == "project name" and values[RUN] == "plate id"
    assert find_leaks(text, values) == [] and find_leaks(config_text, values) == []
    dataset = result.config["datasets"][0]
    assert dataset["qc_warn"] == "Warning" and dataset["qc_warn_share"] == pytest.approx(2 / 30, abs=0.01)
    assert dataset["assays"][2]["below_lod"] == 0.25 and dataset["assays"][0]["lod"] == 1.25
    assert dataset["wide"]["bottom_rows"][2] == {"label": "Normalization", "role": "text", "text": "Intensity"}
    assert dataset["wide"]["software"] == "NPX Manager 2.1.0"


def test_qc_warning_share_is_kept_per_panel(tmp_path: Path) -> None:
    """A panel without QC warnings keeps none, so a compare finds no new level."""
    data = wide_config()
    data["datasets"][0]["wide"] = {"columns": [{"label": "QC Warning", "role": "sample_qc", "warn_share": 0.3},
                                               {"label": "QC Warning", "role": "sample_qc", "warn_share": 0.0}]}
    data["datasets"][0]["qc_warn"] = "Warning"
    first = generate_data(data, tmp_path, "first") / "dataset_1.csv"
    columns = profile([first], Options()).config["datasets"][0]["wide"]["columns"]
    assert columns[0]["warn_share"] > 0 and columns[1]["warn_share"] == 0


def test_generic_labels_hide_assays_and_panels(tmp_path: Path) -> None:
    """With generic labels, assay and panel names leave the config, and the audit knows them."""
    path = write_real_file(tmp_path / "npx_wide.csv")
    options = Options(generic_labels=True)
    config_text = to_yaml(profile([path], options))
    assert "IL6" not in config_text and "Inflammation" not in config_text
    assert find_leaks(config_text, sensitive_values([path], options)) == []


def test_wide_effect_is_found_by_validate(tmp_path: Path) -> None:
    """In the model mode, validate reads the wide file back and finds the planted effect."""
    data = wide_config("model")
    data["design"]["subjects"]["groups"] = {"group_a": 60, "group_b": 60}
    data["design"]["timepoints"] = ["baseline"]
    result = validate(generate_data(data, tmp_path, "out"))
    assert result.issues == []
    planted = [check for check in result.effects if check.planted != 0]
    assert planted and all(check.found for check in planted)


@pytest.mark.parametrize(("change", "message"), [
    ({"columns": [{"name": "SampleID", "kind": "olink", "role": "sample_id"}]}, "no columns"),
    ({"file": "dataset_1.parquet"}, "not .parquet"),
    ({"wide": {"top_rows": ["Panel", "OlinkID"]}}, "'Assay' row"),
])
def test_wide_config_errors(change: dict[str, Any], message: str) -> None:
    """A wide dataset rejects long columns, Parquet and top rows without an Assay row."""
    data = wide_config()
    data["datasets"][0].update(change)
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    assert message in str(caught.value)


def test_long_layout_rejects_the_wide_section() -> None:
    """A long dataset with a wide section fails, so the section is never silently ignored."""
    data = yaml.safe_load(init_config(["olink"]))
    data["datasets"][0]["wide"] = {"data_type": "NPX Data"}
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    assert "set layout: wide" in str(caught.value)


def target_96_assays(count: int, controls: int = 0) -> list[dict[str, Any]]:
    """Return assays of one Target 96 panel, with internal controls at the end.

    Args:
        count: Number of assays.
        controls: Number of internal control columns.

    Returns:
        The assay configs.
    """
    panel = "Olink Target 96 Inflammation"
    assays = [{"assay": f"A{index}", "panel": panel, "quantiles": [1.0, 2.0]} for index in range(count)]
    names = ["Inc Ctrl 1", "Inc Ctrl 2", "Det Ctrl", "Ext Ctrl"][:controls]
    return assays + [{"assay": name, "panel": panel, "quantiles": [1.0, 2.0]} for name in names]


@pytest.mark.parametrize(("count", "controls", "valid"), [(92, 0, True), (92, 3, True), (91, 0, False),
                                                          (3, 0, False)])
def test_target_96_panel_needs_92_assays(count: int, controls: int, valid: bool) -> None:
    """A Target 96 panel of a wide file has 92 assays; internal controls do not count."""
    data = wide_config()
    data["datasets"][0]["assays"] = target_96_assays(count, controls)
    if valid:
        parse_config(data)
        return
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    assert "92 assays" in str(caught.value)
