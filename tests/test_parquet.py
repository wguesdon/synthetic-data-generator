"""Tests of the Parquet format of a plain table."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest

from sdg.audit import find_leaks, sensitive_values
from sdg.cli import main
from sdg.compare import compare
from sdg.config import ConfigError, parse_config
from sdg.formats import detect_module, tabular
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import LabelKey, Options
from sdg.profile import profile, to_yaml
from sdg.report import render
from sdg.validate import validate

#: A pattern that matches no file name, so no table is private by its name.
NO_PRIVATE_FILES = re.compile(r"(?!x)x")

ROWS = 60


def write_visits(path: Path) -> None:
    """Write a Parquet table of visits with typed columns and null cells.

    Args:
        path: The output file.
    """
    start = dt.datetime(2024, 3, 1, 8, 30)
    table = pa.table({
        "subject_id": pa.array([1000 + index % 20 for index in range(ROWS)], pa.int64()),
        "arm": pa.array(["placebo" if index % 3 else "active" for index in range(ROWS)]),
        "crp": pa.array([None if index % 10 == 0 else 1.5 + index * 0.25 for index in range(ROWS)], pa.float64()),
        "visits": pa.array([index % 7 for index in range(ROWS)], pa.int64()),
        "visit_day": pa.array([dt.date(2024, 3, 1) + dt.timedelta(days=index) for index in range(ROWS)]),
        "sampled_at": pa.array([start + dt.timedelta(hours=5 * index) for index in range(ROWS)], pa.timestamp("us")),
        "fasting": pa.array([index % 4 == 0 for index in range(ROWS)]),
    })
    parquet.write_table(table, path)


def test_glimpse_reads_a_plain_parquet_table(tmp_path: Path) -> None:
    """A Parquet table that is not an Olink export reads as a plain table with typed cells."""
    path = tmp_path / "warehouse_dump.parquet"
    write_visits(path)
    assert detect_module(path) is tabular
    report = glimpse([path], Options(private_files=NO_PRIVATE_FILES))
    table = report.tables[0]
    assert table.format == "parquet"
    assert [column.kind for column in table.columns] == [
        "identifier", "categorical", "numeric", "numeric", "date", "date", "boolean"]


def test_parquet_round_trip(tmp_path: Path) -> None:
    """Profile, generate, glimpse and compare: the synthetic Parquet file has the real structure and types."""
    path = tmp_path / "warehouse_dump.parquet"
    write_visits(path)
    options = Options(private_files=NO_PRIVATE_FILES)
    result = profile([path], options)
    dataset = result.config["datasets"][0]
    assert dataset["format"] == "parquet" and dataset["file"] == "file_1.parquet"
    text = to_yaml(result)
    assert find_leaks(text, sensitive_values([path], options)) == []
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    generate(config, tmp_path / "out")
    written = parquet.read_table(tmp_path / "out" / "file_1.parquet")
    assert written.column_names == ["subject_id", "arm", "crp", "visits", "visit_day", "sampled_at", "fasting"]
    types = dict(zip(written.column_names, written.schema.types, strict=True))
    assert types["subject_id"] == pa.int64() and types["crp"] == pa.float64() and types["visits"] == pa.int64()
    assert types["visit_day"] == pa.date32() and pa.types.is_timestamp(types["sampled_at"])
    assert types["fasting"] == pa.bool_() and pa.types.is_string(types["arm"])
    assert written.num_rows == ROWS and written.column("crp").null_count == 6
    assert validate(tmp_path / "out").issues == []
    synthetic = glimpse([tmp_path / "out" / "file_1.parquet"], options)
    errors = [item for item in compare(glimpse([path], options), synthetic) if item.level == "error"]
    assert errors == []


def test_parquet_dataset_has_no_rows_around_the_table() -> None:
    """A Parquet dataset refuses a delimiter and the rows above or below a table."""
    base = {"version": 1, "seed": 1, "datasets": [{"name": "t", "format": "parquet", "file": "t.parquet",
                                                    "sheets": [{"rows": 3, "columns": [
                                                        {"name": "v", "kind": "numeric", "quantiles": [0, 1]}]}]}]}
    parse_config(base)
    base["datasets"][0]["sheets"][0]["preamble"] = [{"cells": 1}]
    with pytest.raises(ConfigError, match="preamble"):
        parse_config(base)
    del base["datasets"][0]["sheets"][0]["preamble"]
    base["datasets"][0]["delimiter"] = ";"
    with pytest.raises(ConfigError, match="delimiter"):
        parse_config(base)


def test_relabel_copies_a_parquet_table(tmp_path: Path) -> None:
    """A relabeled Parquet copy has the generic column names and levels, and keeps the cell types."""
    path = tmp_path / "warehouse_dump.parquet"
    write_visits(path)
    options = Options(private_files=NO_PRIVATE_FILES, generic_labels=True, key=LabelKey())
    render(glimpse([path], options))
    key = tmp_path / "key.csv"
    options.key.write(key)
    assert main(["relabel", str(key), str(path), "-o", str(tmp_path / "copies")]) == 0
    copy = parquet.read_table(tmp_path / "copies" / "file_1.parquet")
    assert copy.column_names == ["identifier_1", "categorical_1", "numeric_1", "numeric_2", "date_1", "date_2",
                                 "boolean_1"]
    assert set(copy.column("categorical_1").to_pylist()) == {"level_1", "level_2"}
    assert copy.schema.field("numeric_1").type == pa.float64()
