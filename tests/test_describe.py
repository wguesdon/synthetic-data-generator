"""Tests of ``sdg describe design``, ``sdg describe groups`` and the DataFrame functions."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from sdg import describe
from sdg.audit import find_leaks, sensitive_values
from sdg.cli import main
from sdg.describe import describe_file
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.report import fact_map, parse, render

#: A design with a known ICC, missing share and effect. Every name is invented.
KNOWN_DESIGN = {
    "version": 1, "seed": 1, "mode": "model",
    "design": {"subjects": {"prefix": "PT-", "groups": {"control": 40, "patient": 40}},
               "timepoints": ["day_0", "day_7", "day_28"], "icc": 0.6, "missing_samples": 0.1},
    "datasets": [{"name": "panel", "format": "csv", "file": "panel.csv", "sheets": [{
        "name": "sheet_1", "layout": "wide", "columns": [
            {"name": "Patient ID", "kind": "design", "field": "subject"},
            {"name": "disease_status", "kind": "design", "field": "group"},
            {"name": "Visit", "kind": "design", "field": "timepoint"},
            {"name": "CD8", "kind": "numeric", "quantiles": [5.0, 10.0, 15.0, 20.0, 30.0], "decimals": 2},
            {"name": "CD4", "kind": "numeric", "quantiles": [20.0, 30.0, 40.0, 50.0, 60.0], "decimals": 2},
            {"name": "CD56", "kind": "numeric", "quantiles": [1.0, 3.0, 5.0, 8.0, 12.0], "decimals": 2}]}]}],
    "effects": [{"dataset": "panel", "feature": "CD8", "contrast": ["patient", "control"], "fold_change": 2.0,
                 "timepoints": ["day_28"]}],
}


@pytest.fixture
def panel(tmp_path: Path) -> Path:
    """Generate the known design.

    Args:
        tmp_path: The pytest temporary folder.

    Returns:
        The CSV file, one row per sample.
    """
    config = tmp_path / "known.yaml"
    config.write_text(yaml.safe_dump(KNOWN_DESIGN, sort_keys=False), encoding="utf-8")
    generate(config, tmp_path / "known")
    return tmp_path / "known" / "panel.csv"


def lines(report_text: str) -> dict[tuple[str, str], dict[str, str]]:
    """Index the column lines of a description by name and timepoint.

    Args:
        report_text: The text of a description.

    Returns:
        The facts of each line, by its name and its ``timepoint`` fact.
    """
    table = parse(report_text).tables[0]
    found = {}
    for column in table.columns:
        facts = fact_map(column.facts)
        found[(column.name, facts.get("timepoint", ""))] = facts
    return found


def ratio(text: str) -> float:
    """Read the ratio from a contrast fact.

    Args:
        text: Text such as ``ratio 1.92, log2 ratio 0.94, difference 12.4``.

    Returns:
        The ratio.
    """
    return float(text.split(",")[0].removeprefix("ratio "))


def test_design_recovers_the_known_design(panel: Path) -> None:
    """The roles come from the names, and the counts, the order, the ICC and the missing share match."""
    table = describe_file("design", panel, {}, Options()).tables[0]
    facts = fact_map(table.facts)
    assert facts["subject column"] == "'Patient ID'" and facts["group column"] == "'disease_status'"
    assert facts["timepoint column"] == "'Visit'" and facts["subjects"] == "80" and facts["features"] == "3"
    assert 0.45 <= float(facts["icc"].split()[0]) <= 0.75
    assert 0.0 <= float(facts["missing_samples"]) <= 0.2 and facts["replicates"] == "none"
    groups = {column.name: fact_map(column.facts)["subjects"] for column in table.columns if column.kind == "group"}
    assert groups == {"control": "40", "patient": "40"}
    assert [column.name for column in table.columns if column.kind == "timepoint"] == ["day_0", "day_7", "day_28"]


def test_groups_find_the_planted_effect(panel: Path) -> None:
    """The control level is the reference, and only the planted timepoint shows a ratio near 2."""
    text = render(describe_file("groups", panel, {}, Options()))
    assert "reference: 'control'" in text
    found = lines(text)
    assert 1.6 <= ratio(found[("CD8", "'day_28'")]["'patient' vs 'control'"]) <= 2.6
    assert 0.7 <= ratio(found[("CD8", "'day_0'")]["'patient' vs 'control'"]) <= 1.4
    assert found[("CD4", "'day_0'")]["'control'"].startswith("n 40, median ")


def test_small_group_prints_no_statistic() -> None:
    """A group with less than 10 samples prints its size as <10, and no contrast."""
    frame = pd.DataFrame({"arm": ["a"] * 12 + ["b"] * 6, "IL6": [float(index) for index in range(1, 19)]})
    found = lines(describe.groups(frame, group="arm"))
    assert found[("IL6", "")]["'b'"] == "n <10"
    assert not any(" vs " in key for key in found[("IL6", "")])


def test_private_table_prints_counts_only(panel: Path) -> None:
    """Rule 2: a table with an age column prints no ICC, no median and no ratio."""
    frame = pd.read_csv(panel)
    frame["Age"] = 40
    design_text = describe.design(frame)
    assert "private: counts only" in design_text and "icc" not in design_text
    assert "subjects: 80" in design_text and "'level_1' <group>" in design_text
    groups_text = describe.groups(frame)
    assert "private: counts only" in groups_text and "median" not in groups_text


def test_generic_labels_match_the_glimpse(panel: Path) -> None:
    """With generic labels, the roles print the glimpse names and no real name or level leaks."""
    options = Options(generic_labels=True)
    text = render(describe_file("design", panel, {}, options)) + render(describe_file("groups", panel, {}, options))
    names = [column.name for column in glimpse([panel], options).tables[0].columns]
    assert names[:3] == ["identifier_1", "categorical_1", "categorical_2"]
    assert "subject column: 'identifier_1'" in text and "group column: 'categorical_1'" in text
    assert "'numeric_1' <numeric>" in text
    for real in ("Patient ID", "disease_status", "CD8", "patient", "control", "day_28"):
        assert real not in text
    assert find_leaks(text, sensitive_values([panel], options)) == []


def test_long_table_reads_features_and_replicates(tmp_path: Path) -> None:
    """A long table with a feature and a value column has features and replicates."""
    root = Path(__file__).resolve().parents[1]
    generate(root / "example/configs/assay_long.yaml", tmp_path / "long")
    path = tmp_path / "long" / "assay_long.csv"
    facts = fact_map(describe_file("design", path, {}, Options()).tables[0].facts)
    assert facts["feature column"] == "'Analyte'" and facts["value column"] == "'Concentration'"
    assert facts["features"] == "5" and facts["replicates.n"] == "2" and "replicates.cv_pct" in facts
    report = describe_file("groups", path, {}, Options(), reference="arm_b")
    assert fact_map(report.tables[0].facts)["reference"] == "'arm_b'"
    assert {column.kind for column in report.tables[0].columns} == {"feature"}


def test_timepoints_follow_study_order() -> None:
    """Baseline comes first, then the number in each label, whatever the row order."""
    frame = pd.DataFrame({"Patient": [f"P{index}" for index in range(3) for _ in range(3)],
                          "Visit": ["Week 12", "Baseline", "Week 4"] * 3})
    table = parse(describe.design(frame)).tables[0]
    assert [column.name for column in table.columns if column.kind == "timepoint"] == ["Baseline", "Week 4", "Week 12"]


def test_dataframe_glimpse_matches_the_file_glimpse(panel: Path) -> None:
    """The DataFrame glimpse gives the names, kinds and format of the file glimpse; an index becomes a column."""
    frame = pd.read_csv(panel)
    from_frame = parse(describe.glimpse(frame.set_index("Patient ID"), name="panel.csv")).tables[0]
    from_file = glimpse([panel], Options()).tables[0]
    assert [(column.name, column.kind) for column in from_frame.columns] == \
        [(column.name, column.kind) for column in from_file.columns]
    assert from_frame.label == "file_1.csv" and from_frame.format == "csv"


def test_cli_describe(panel: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The command prints a report, writes one with -o, and fails on an unknown column or reference."""
    assert main(["describe", "design", str(panel)]) == 0
    assert "subjects: 80" in capsys.readouterr().out
    assert main(["describe", "groups", str(panel), "--timepoint", "", "-o", str(tmp_path)]) == 0
    written = next(tmp_path.glob("groups_*.txt")).read_text(encoding="utf-8")
    assert "timepoint column: none" in written and "repeated rows: averaged per sample" in written
    assert main(["describe", "groups", str(panel), "--group", "nope"]) == 1
    assert "no column named 'nope'" in capsys.readouterr().err
    assert main(["describe", "groups", str(panel), "--reference", "placebo"]) == 1
    assert "no group named 'placebo'" in capsys.readouterr().err


def test_treatments_within_a_subject_are_samples() -> None:
    """Three treatments of one subject are three samples, not three replicates."""
    subjects = [f"S{index:02d}" for index in range(12)]
    frame = pd.DataFrame({"Subject": [subject for subject in subjects for _ in range(3)],
                          "Treatment": ["vehicle", "low", "high"] * 12,
                          "IL6": [float(index % 7 + 1) for index in range(36)]})
    facts = fact_map(parse(describe.design(frame)).tables[0].facts)
    assert facts["samples"] == "36" and facts["replicates"] == "none"
    assert facts["subjects in several groups"] == "12"
    found = lines(describe.groups(frame))
    assert all(found[("IL6", "")][f"'{level}'"].startswith("n 12,") for level in ("vehicle", "low", "high"))


def test_long_table_group_holds_one_value_per_sample() -> None:
    """In a long table, a column that changes with the assay, such as OlinkID, is no group candidate."""
    samples = [f"S{index:02d}" for index in range(12)]
    frame = pd.DataFrame({"SampleID": [sample for sample in samples for _ in range(2)],
                          "Assay": ["IL6", "TNF"] * 12, "OlinkID": ["OID1", "OID2"] * 12,
                          "QC_Warning": [("PASS" if index % 4 else "WARN") for index in range(12) for _ in range(2)],
                          "NPX": [float(index % 5) for index in range(24)]})
    facts = fact_map(parse(describe.design(frame)).tables[0].facts)
    assert facts["feature column"] == "'Assay'" and facts["group candidates"] == "'QC_Warning'"


def test_reports_pass_the_audit(panel: Path) -> None:
    """A design and a groups report hold no identifier, and the audit of the real file passes."""
    text = render(describe_file("design", panel, {}, Options())) + render(describe_file("groups", panel, {}, Options()))
    values = sensitive_values([panel], Options())
    assert "PT-001" in values and find_leaks(text, values) == []


def test_profile_of_a_dataframe_matches_the_profile_of_its_file(tmp_path: Path) -> None:
    """describe.profile gives the config that sdg profile gives for the same table as a CSV file."""
    frame = pd.DataFrame({"sample_id": [f"S{index:03d}" for index in range(40)],
                          "arm": ["drug", "placebo"] * 20,
                          "crp": [0.5 + index * 0.13 for index in range(40)]})
    path = tmp_path / "trial.csv"
    frame.to_csv(path, index=False)
    assert main(["profile", str(path), "--seed", "7", "-o", str(tmp_path / "file.yaml")]) == 0
    from_file = yaml.safe_load((tmp_path / "file.yaml").read_text(encoding="utf-8"))
    from_frame = yaml.safe_load(describe.profile(frame, name="trial.csv", seed=7))
    assert from_frame == from_file
    text = describe.profile(frame, name="trial.parquet", generic_labels=True, label_key=tmp_path / "key.csv")
    config = yaml.safe_load(text)
    assert config["datasets"][0]["format"] == "parquet" and "arm" not in text and "placebo" not in text
    assert "placebo" in (tmp_path / "key.csv").read_text(encoding="utf-8")
    assert find_leaks(text, sensitive_values([path], Options(generic_labels=True))) == []
    generate_path = tmp_path / "config.yaml"
    generate_path.write_text(text, encoding="utf-8")
    assert generate(generate_path, tmp_path / "out")
