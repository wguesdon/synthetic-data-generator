"""Tests of privacy rule 2: the private columns record counts only, the others safe facts only."""

from __future__ import annotations

from pathlib import Path

from sdg.audit import find_leaks, sensitive_values
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import LabelKey, Options
from sdg.profile import profile, to_yaml
from sdg.report import fact_map, render
from sdg.tables import read_tables

ROWS = 80
COUNTS_ONLY = {"filled", "empty", "unique", "cell types", "non-numeric"}


def write_patients(path: Path) -> list[float]:
    """Write an invented patient table with an age column, which makes it private.

    Args:
        path: The CSV file.

    Returns:
        The creatinine values, for the checks of the trimmed quantiles.
    """
    creatinine = [round(0.5 + (index * 37 % ROWS) * 0.11, 2) for index in range(ROWS)]
    lines = ["patient_id,age,anaemia,smoking,creatinine,ejection,score,fasting,rare_flag"]
    for index in range(ROWS):
        smoking = 2 if index < 5 else index % 2
        lines.append(",".join([
            str(1001 + index), str(40 + index % 45), str(int(index % 5 < 2)), str(smoking), str(creatinine[index]),
            str(20 + 5 * (index % 15)), str(index % 12) if index < 30 else "", "TRUE" if index % 3 else "FALSE",
            "TRUE" if index < 3 else "FALSE"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return creatinine


def test_report_prints_counts_for_private_columns_and_safe_facts_for_the_others(tmp_path: Path) -> None:
    """The age prints counts only; codes, trimmed quantiles, shapes and true counts print for the others."""
    path = tmp_path / "patients.csv"
    write_patients(path)
    report = glimpse([path], Options())
    table = report.tables[0]
    assert fact_map(table.facts)["private columns"] == "'age'"
    columns = {column.name: fact_map(column.facts) for column in table.columns}
    assert set(columns["age"]) <= COUNTS_ONLY
    assert columns["patient_id"]["shapes"] == "'9999' 80"
    assert columns["anaemia"]["codes"] == "'0' 48, '1' 32"
    assert columns["smoking"]["codes"] == "'1' 38, '0' 37"
    assert "range" not in columns["creatinine"] and columns["creatinine"]["5% to 95%"].count(" to ") == 1
    assert "codes" not in columns["ejection"] and "5% to 95%" in columns["ejection"]
    assert columns["score"]["numbers"] == "30"
    assert columns["fasting"]["true"] == "53" and "true" not in columns["rare_flag"]
    text = render(report)
    assert find_leaks(text, sensitive_values([path], Options())) == []


def test_profile_keeps_safe_facts_and_no_minimum_or_maximum(tmp_path: Path) -> None:
    """The profile writes codes as exact values, quantiles from 5% to 95%, and defaults for the age."""
    path = tmp_path / "patients.csv"
    creatinine = write_patients(path)
    result = profile([path], Options())
    columns = {column["name"]: column for column in result.config["datasets"][0]["sheets"][0]["columns"]}
    assert columns["age"]["quantiles"] == [0.0, 100.0]
    assert columns["patient_id"]["pattern"] == "9999"
    assert columns["anaemia"]["exact"] == {0.0: 0.6, 1.0: 0.4} and columns["anaemia"]["quantiles"] == [0.0, 1.0]
    assert set(columns["smoking"]["exact"]) == {0.0, 1.0}
    quantiles = columns["creatinine"]["quantiles"]
    assert len(quantiles) == 11 and min(creatinine) < quantiles[0] and quantiles[-1] < max(creatinine)
    assert columns["score"]["quantiles"] == [0.0, 100.0]
    assert columns["fasting"]["true_share"] == 0.6625 and columns["rare_flag"]["true_share"] == 0.5
    assert "datasets[0].sheets[0].columns[1].quantiles" in result.defaults
    text = to_yaml(result)
    assert find_leaks(text, sensitive_values([path], Options())) == []
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    generate(config, tmp_path / "out")
    table = read_tables(tmp_path / "out" / "file_1.csv")[0]
    assert set(table.column(2)) == {"0", "1"} and set(table.column(3)) == {"0", "1"}
    values = [float(cell) for cell in table.column(4)]
    assert quantiles[0] <= min(values) and max(values) <= quantiles[-1]


def test_generic_report_of_a_private_table_keys_its_levels(tmp_path: Path) -> None:
    """With generic labels, a private table prints generic level counts and the key maps them."""
    lines = ["Sex,arm"] + [f"{'F' if index % 2 else 'M'},{'drug' if index % 3 else 'placebo'}" for index in range(30)]
    path = tmp_path / "trial.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    options = Options(generic_labels=True, key=LabelKey())
    report = glimpse([path], options)
    columns = [fact_map(column.facts) for column in report.tables[0].columns]
    assert columns[1]["values"] == "'level_1' 20, 'level_2' 10"
    assert fact_map(report.tables[0].facts)["private columns"] == "'categorical_1'"
    assert "drug" not in render(report) and "Sex" not in render(report)
    levels = {(row[3], row[4], row[5]) for row in options.key.rows if row[0] == "level"}
    assert ("categorical_2", "level_1", "drug") in levels and ("categorical_2", "level_2", "placebo") in levels


def test_clinical_score_column_makes_a_table_private(tmp_path: Path) -> None:
    """Rule 2: a column named after a clinical score, such as ECOG or NYHA, is private and records counts only."""
    path = tmp_path / "scores.csv"
    lines = ["record_id,ECOG,NYHA class,creatinine"]
    lines += [f"R{100 + index},{index % 5},{index % 4 + 1},{0.6 + index * 0.01:.2f}" for index in range(ROWS)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    table = glimpse([path], Options()).tables[0]
    assert fact_map(table.facts)["private columns"] == "'ECOG', 'NYHA class'"
    columns = {column.name: fact_map(column.facts) for column in table.columns}
    assert set(columns["ECOG"]) <= COUNTS_ONLY and set(columns["NYHA class"]) <= COUNTS_ONLY
    assert "5% to 95%" in columns["creatinine"]
