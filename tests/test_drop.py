"""Tests of the --drop option, which leaves columns out of the report and the config."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sdg.cli import main
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile
from sdg.report import fact_map, render


def write_contacts(path: Path) -> None:
    """Write an invented table with an email column and an age column.

    Args:
        path: The CSV file.
    """
    rows = [f"P{index:03d},{30 + index % 40},ann{index}@clinic-example.org,{1.5 + index * 0.1:.2f}"
            for index in range(60)]
    path.write_text("patient_id,age,contact,crp\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_dropped_column_is_left_out_of_the_report_and_the_config(tmp_path: Path,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """A dropped column is in no report, description or config, and the audit of the report passes."""
    path = tmp_path / "contacts.csv"
    write_contacts(path)
    options = Options(drop=re.compile("^contact$"))
    report = glimpse([path], options)
    assert [column.name for column in report.tables[0].columns] == ["patient_id", "age", "crp"]
    config = profile([path], options).config
    assert [column["name"] for column in config["datasets"][0]["sheets"][0]["columns"]] == ["patient_id", "age",
                                                                                             "crp"]
    assert main(["report", str(path), "--drop", "^contact$", "-o", str(tmp_path / "reports") + "/"]) == 0
    written = next((tmp_path / "reports").glob("report_*.txt"))
    assert "contact" not in written.read_text(encoding="utf-8")
    assert main(["audit", str(written), str(path), "--drop", "^contact$"]) == 0
    assert main(["describe", "design", str(path), "--drop", "^contact$"]) == 0
    assert "contact" not in capsys.readouterr().out


def test_dropping_the_private_column_makes_the_table_public(tmp_path: Path) -> None:
    """A table whose only private column is dropped is no longer private, as a DataFrame without it is not."""
    path = tmp_path / "contacts.csv"
    write_contacts(path)
    kept = glimpse([path], Options())
    dropped = glimpse([path], Options(drop=re.compile("^age$")))
    assert fact_map(kept.tables[0].facts)["private"] == "safe facts"
    assert "private" not in fact_map(dropped.tables[0].facts)
    assert "age" not in render(dropped)
