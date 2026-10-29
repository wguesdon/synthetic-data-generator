"""Tests of ``sdg profile`` and ``sdg audit``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import SUBJECTS
from sdg.audit import find_leaks, sensitive_values
from sdg.cli import main
from sdg.config import load_config
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import render


def dataset(config: dict[str, Any], file: str) -> dict[str, Any]:
    """Find one dataset of a profiled config by its output file.

    Args:
        config: The profiled config.
        file: Output file name.

    Returns:
        The dataset config.
    """
    return next(item for item in config["datasets"] if item["file"] == file)


def columns(config: dict[str, Any], file: str) -> dict[str, dict[str, Any]]:
    """Index the columns of the first sheet of one dataset by name.

    Args:
        config: The profiled config.
        file: Output file name.

    Returns:
        Each column config by name.
    """
    return {column["name"]: column for column in dataset(config, file)["sheets"][0]["columns"]}


def test_profile_records_the_delivery_structure(delivery: Path) -> None:
    """Kinds, delimiter, tokens, exact values and stray spaces survive."""
    config = profile([delivery], Options()).config
    assert [item["file"] for item in config["datasets"]] == ["file_1.xlsx", "file_2.xlsx", "file_3.csv"]
    plate = dataset(config, "file_3.csv")
    assert plate["delimiter"] == ";"
    found = columns(config, "file_3.csv")
    assert {name: column["kind"] for name, column in found.items()} == {
        "Sample ID": "identifier", "Dilution": "categorical", "OD450": "numeric",
        "Comment": "text", "Detected": "boolean", "Run date": "date"}
    assert found["Sample ID"]["pattern"] == "AAA-9999" and found["Sample ID"]["distinct"] == 16
    assert set(found["Dilution"]["levels"]) == {"neat", "diluted "}
    reading = found["OD450"]
    assert len(reading["quantiles"]) == 11 and reading["decimals"] == 2
    assert set(reading["tokens"]) == {"NA"} and 0.0 in reading["exact"]
    assert found["Run date"]["interval_days"][-1] == 8.0


def test_workbook_text_numbers_and_dates(delivery: Path) -> None:
    """Numbers stored as text keep that quirk; workbook dates stay date cells."""
    found = columns(profile([delivery], Options()).config, "file_1.xlsx")
    assert found["IL6"]["as_text"] is True
    assert found["Measured on"]["kind"] == "date" and "as_text" not in found["Measured on"]
    assert dataset(profile([delivery], Options()).config, "file_1.xlsx")["sheets"][0]["name"] == "Results"


def test_private_table_gets_defaults(delivery: Path) -> None:
    """The clinical workbook records counts only, and the header lists each default."""
    result = profile([delivery], Options())
    found = columns(result.config, "file_2.xlsx")
    assert found["Age"]["quantiles"] == [0.0, 100.0]
    assert set(found["Sex"]["levels"]) == {"level_1", "level_2"}
    assert found["Subject"]["pattern"] == "AAA-9999"
    assert "datasets[1].sheets[0].columns[1].quantiles" in result.defaults
    assert "#   datasets[1].sheets[0].columns[1].quantiles" in to_yaml(result)


def test_profile_yaml_loads_and_passes_the_audit(delivery: Path, tmp_path: Path) -> None:
    """The written YAML validates and holds no real identifier, text, date or file name."""
    result = profile([delivery], Options(), seed=5)
    path = tmp_path / "config.yaml"
    path.write_text(to_yaml(result), encoding="utf-8")
    assert load_config(path).seed == 5
    values = sensitive_values([delivery], Options())
    assert SUBJECTS[0] in values and "plate" in values
    assert find_leaks(path.read_text(encoding="utf-8"), values) == []


def test_generic_labels_hide_names(delivery: Path) -> None:
    """Generic labels hide column names, sheet names and levels."""
    text = to_yaml(profile([delivery], Options(generic_labels=True)))
    assert "Dilution" not in text and "neat" not in text and "Results" not in text
    options = Options(generic_labels=True)
    assert find_leaks(text, sensitive_values([delivery], options)) == []


def test_audit_finds_a_planted_identifier(delivery: Path, tmp_path: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    """A config with a real subject identifier fails the audit (criterion 3)."""
    leaked = to_yaml(profile([delivery], Options())).replace("AAA-9999", SUBJECTS[4], 1)
    path = tmp_path / "leaked.yaml"
    path.write_text(leaked, encoding="utf-8")
    assert main(["audit", str(path), str(delivery)]) == 1
    assert SUBJECTS[4] in capsys.readouterr().out


def test_audit_finds_a_sequence() -> None:
    """Rule 9: a run of 20 nucleotides fails, whatever its source."""
    findings = find_leaks("pattern: ACGTACGTACGTACGTACGTAC\n", {})
    assert [finding.kind for finding in findings] == ["sequence"]


def test_audit_matches_short_values_as_words() -> None:
    """A three-letter value matches only as a whole word."""
    values = {"P01": "identifier"}
    assert find_leaks("plate: P01\n", values)[0].line == 1
    assert find_leaks("pattern: AP012\n", values) == []


def test_audit_passes_on_a_glimpse_report(delivery: Path, tmp_path: Path) -> None:
    """A glimpse report of the delivery passes the audit."""
    report = tmp_path / "report"
    assert main(["glimpse", str(delivery), "-o", str(report)]) == 0
    written = next(tmp_path.glob("report_*.txt"))
    assert main(["audit", str(written), str(delivery)]) == 0


def test_cli_profile_prints_the_mapping_to_stderr(delivery: Path, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """The real file names go to stderr only, never into the config."""
    target = tmp_path / "config.yaml"
    assert main(["profile", str(delivery), "-o", str(target)]) == 0
    captured = capsys.readouterr()
    assert "file_3.csv = " in captured.err and "plate.csv" in captured.err
    assert "plate" not in target.read_text(encoding="utf-8")


def test_private_table_keeps_missing_tokens_only(tmp_path: Path) -> None:
    """A private table keeps NA-like tokens; another token becomes a missing cell."""
    rows = [[f"{40 + index}", "NA" if index % 5 == 0 else ("refused" if index % 7 == 0 else "1.5")]
            for index in range(30)]
    path = tmp_path / "demo.csv"
    path.write_text("Age,score\n" + "\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
    score = columns(profile([path], Options()).config, "file_1.csv")["score"]
    assert set(score["tokens"]) == {"NA"}
    assert score["missing"] > 0


def test_generic_levels_keep_missing_tokens(tmp_path: Path) -> None:
    """A missing level such as NA keeps its text; a band such as <18 becomes generic."""
    statuses = ["NA", "NA", "NA", "remission", "relapse", "?"] * 5
    bands = ["<18", "18-65", ">65"] * 10
    path = tmp_path / "demo.csv"
    path.write_text("status,band\n" + "\n".join(f"{status},{band}" for status, band in zip(statuses, bands))
                    + "\n", encoding="utf-8")
    found = columns(profile([path], Options(generic_labels=True)).config, "file_1.csv")
    assert set(found["categorical_1"]["levels"]) == {"NA", "?", "level_1", "level_2"}
    assert set(found["categorical_2"]["levels"]) == {"level_1", "level_2", "level_3"}
    report = render(glimpse([path], Options(generic_labels=True)))
    assert "'NA' 15" in report and "'?' 5" in report and "remission" not in report and "<18" not in report


def test_private_table_keeps_missing_levels(tmp_path: Path) -> None:
    """A private table keeps a missing level, so the synthetic column keeps its missing cells."""
    rows = [f"{40 + index},{'NA' if index % 3 == 0 else ('M' if index % 2 else 'F')}" for index in range(30)]
    path = tmp_path / "demo.csv"
    path.write_text("Age,Sex\n" + "\n".join(rows) + "\n", encoding="utf-8")
    sex = columns(profile([path], Options()).config, "file_1.csv")["Sex"]
    assert set(sex["levels"]) == {"NA", "level_1", "level_2"}


def test_audit_matches_file_names_as_words() -> None:
    """A file stem matches only as a whole word, and a schema word never matches."""
    values = {"plate": "file name"}
    assert find_leaks("file: plate_export.csv\n", values) == []
    assert find_leaks("name: plate\n", values)[0].kind == "file name"


def test_audit_skips_schema_words(tmp_path: Path) -> None:
    """A real file named like a format, such as counts.tsv, is not a leak."""
    path = tmp_path / "counts.csv"
    path.write_text("x\n1\n", encoding="utf-8")
    assert "counts" not in sensitive_values([path], Options())


def test_audit_skips_missing_tokens_and_text_inside_printed_levels(tmp_path: Path) -> None:
    """Free text inside a printed level and a "#N/A" cell are not leaks; an identifier inside a level is."""
    sites = ["Lung", *[f"site note {index}" for index in range(24)], "#N/A", "#N/A", "#N/A", "#N/A", "#N/A"]
    rows = [[f"PT-{index:03d}", "Lung adenocarcinoma" if index % 2 else "Breast carcinoma",
             "PT-001 rerun" if index % 2 else "standard", sites[index]] for index in range(30)]
    path = tmp_path / "tumours.csv"
    path.write_text("patient_id,type,batch,site_other\n" + "\n".join(",".join(row) for row in rows) + "\n",
                    encoding="utf-8")
    values = sensitive_values([path], Options())
    assert "Lung" not in values and "#N/A" not in values
    assert values["site note 3"] == "free text" and values["PT-001"] == "identifier"
    findings = find_leaks(render(glimpse([path], Options())), values)
    assert [(finding.kind, finding.value) for finding in findings] == [("identifier", "PT-001")]


def test_audit_matches_a_number_as_a_quoted_item() -> None:
    """A number such as an identifier 152 or a cell 3.0 matches as a level, never as a count or a version."""
    values = {"152": "identifier", "3.0": "label"}
    assert find_leaks("# sdg glimpse 0.3.0\nrows: 1152; unique: 152\n", values) == []
    assert find_leaks("values: '152' 3\n", values)[0].value == "152"
    assert find_leaks(yaml.safe_dump({"levels": {"3.0": 0.5}}), values)[0].value == "3.0"


def test_audit_matches_digits_with_separators_as_a_token() -> None:
    """The MIMIC lab value 6-10 is not in the date 2026-10-12, but a level 6-10 is a finding."""
    values = {"6-10": "free text"}
    assert find_leaks("# Written by sdg profile 0.3.0 on 2026-10-12.\n", values) == []
    assert find_leaks(yaml.safe_dump({"levels": {"6-10": 0.5}}), values)[0].value == "6-10"


def test_audit_skips_a_value_that_is_its_own_shape_but_not_a_date() -> None:
    """A cell 999 or the redaction mark ___ prints as its own shape; a date always matches."""
    assert find_leaks("shapes: '999' 4, '___' 2\n", {"999": "free text", "___": "free text"}) == []
    assert find_leaks("on 2026-10-12.\n", {"2026-10-12": "date"})[0].kind == "date"
