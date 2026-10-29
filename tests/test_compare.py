"""Tests of ``sdg compare``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from sdg.cli import main
from sdg.compare import compare
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile
from sdg.report import ColumnReport, Fact, Report, TableReport


def round_trip(delivery: Path, tmp_path: Path, edit: dict | None = None) -> tuple[Report, Report]:
    """Glimpse the delivery, profile it, generate, and glimpse the output.

    Args:
        delivery: The invented delivery folder.
        tmp_path: The pytest temporary folder.
        edit: Changes to the CSV dataset config before generation.

    Returns:
        The real report and the synthetic report.
    """
    config = profile([delivery], Options()).config
    config["datasets"][2].update(edit or {})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    generate(path, tmp_path / "out")
    return glimpse([delivery], Options()), glimpse([tmp_path / "out"], Options())


def table(columns: list[ColumnReport], facts: list[Fact] | None = None) -> Report:
    """Build a one-table report for a test.

    Args:
        columns: The column reports.
        facts: The table facts.

    Returns:
        The report.
    """
    return Report(tables=[TableReport("file_1.csv", "csv", facts or [Fact("rows", "10")], columns)])


def test_round_trip_has_no_error(delivery: Path, tmp_path: Path) -> None:
    """Criterion 2: profile and generate keep every structure fact."""
    real, synthetic = round_trip(delivery, tmp_path)
    assert [item for item in compare(real, synthetic) if item.level == "error"] == []


def test_renamed_column_is_an_error(delivery: Path, tmp_path: Path) -> None:
    """An invented column name that differs from the delivered one is an error."""
    config = profile([delivery], Options()).config
    sheet = config["datasets"][2]["sheets"][0]
    sheet["columns"][2]["name"] = "Reading 1"
    real, synthetic = round_trip(delivery, tmp_path, {"sheets": [sheet]})
    messages = [item.message for item in compare(real, synthetic) if item.level == "error"]
    assert messages == ["name: real 'OD450', synthetic 'Reading 1'"]


def test_delimiter_is_an_error(delivery: Path, tmp_path: Path) -> None:
    """A comma where the delivery has a semicolon is an error."""
    real, synthetic = round_trip(delivery, tmp_path, {"delimiter": ","})
    assert any(item.message == "delimiter: real ';', synthetic ','" for item in compare(real, synthetic))


def test_kind_and_level_differences() -> None:
    """A kind change and a missing common level are errors; a missing rare level is a note."""
    real = table([ColumnReport("a", "numeric"),
                  ColumnReport("b", "categorical", [Fact("values", "'x' 60, 'y' 38, 'z' 2")])])
    synthetic = table([ColumnReport("a", "text"), ColumnReport("b", "categorical", [Fact("values", "'x' 100")])])
    found = {(item.level, item.message) for item in compare(real, synthetic)}
    assert ("error", "kind: real numeric, synthetic text") in found
    assert ("error", "values: the synthetic data lacks 'y'") in found
    assert ("note", "values: the synthetic data lacks 'z'") in found


def test_int_and_float_cells_are_one_type() -> None:
    """A workbook gives int or float by value, so the two count as one type."""
    real = table([ColumnReport("a", "numeric", [Fact("cell types", "float 9, int 1")])])
    synthetic = table([ColumnReport("a", "numeric", [Fact("cell types", "float 10")])])
    assert compare(real, synthetic) == []
    synthetic = table([ColumnReport("a", "numeric", [Fact("cell types", "str 10")])])
    assert compare(real, synthetic)[0].message.startswith("cell types")


def test_extra_and_missing_columns_and_tables() -> None:
    """A missing column and a missing table are errors."""
    real = table([ColumnReport("a", "numeric"), ColumnReport("b", "numeric")])
    synthetic = Report(tables=real.tables[:1] * 2)
    synthetic.tables[0] = TableReport("x.csv", "csv", [Fact("rows", "10")], [ColumnReport("a", "numeric")])
    messages = [item.message for item in compare(real, synthetic)]
    assert "tables: real 1, synthetic 2" in messages
    assert "only the real data has 'b'" in messages


def test_cli_exit_code_and_json(delivery: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The command exits 1 on an error and gives JSON on request."""
    real_report = tmp_path / "real.txt"
    synthetic_report = tmp_path / "synthetic.txt"
    real_report.write_text("== file_1.csv <csv>\nrows: 3\n$ 'a' <numeric>\n", encoding="utf-8")
    synthetic_report.write_text("== out.csv <csv>\nrows: 4\n$ 'a' <numeric>\n", encoding="utf-8")
    assert main(["compare", str(real_report), str(synthetic_report), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["errors"] == 1 and result["differences"][0]["message"] == "rows: real 3, synthetic 4"


def test_rare_token_and_its_cell_type_are_notes() -> None:
    """A token in 1 of 40 filled cells is rare: its share is taken over the cells, not over the tokens.

    A token in 2 of 40 cells has a share of 5%, which is not rare.
    """
    real = table([ColumnReport("p", "numeric", [Fact("filled", "40"), Fact("cell types", "float 37, str 3"),
                                                Fact("tokens", "'NA' 2, 'p_value' 1")])])
    synthetic = table([ColumnReport("p", "numeric", [Fact("filled", "40"), Fact("cell types", "float 38, str 2"),
                                                     Fact("tokens", "'NA' 2")])])
    assert [item.level for item in compare(real, synthetic)] == ["note"]
    synthetic = table([ColumnReport("p", "numeric", [Fact("filled", "40"), Fact("cell types", "float 40")])])
    assert [item.level for item in compare(real, synthetic)] == ["error"]
    synthetic = table([ColumnReport("p", "numeric", [Fact("filled", "40"), Fact("cell types", "float 39, str 1"),
                                                     Fact("tokens", "'p_value' 1")])])
    assert ("error", "tokens: the synthetic data lacks 'NA'") in {
        (item.level, item.message) for item in compare(real, synthetic)}


def test_missing_rare_cell_type_is_a_note() -> None:
    """The one text cell of a numeric column can be absent by chance; an extra type is an error."""
    real = table([ColumnReport("p", "numeric", [Fact("filled", "40"), Fact("cell types", "float 39, str 1")])])
    synthetic = table([ColumnReport("p", "numeric", [Fact("filled", "40"), Fact("cell types", "float 40")])])
    assert [item.level for item in compare(real, synthetic)] == ["note"]
    assert [item.level for item in compare(synthetic, real)] == ["error"]


def test_rows_around_the_table_are_errors() -> None:
    """Code that skips 4 rows or the comment lines of the real file fails on another layout."""
    real = table([], [Fact("rows", "10"), Fact("header row", "5"), Fact("comment marks", "'#'"),
                      Fact("footer rows", "1")])
    synthetic = table([], [Fact("rows", "10")])
    keys = {item.message.split(":")[0] for item in compare(real, synthetic) if item.level == "error"}
    assert keys == {"header row", "comment marks", "footer rows"}
