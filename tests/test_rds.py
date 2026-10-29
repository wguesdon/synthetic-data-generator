"""Tests of the RDS format of a plain table (the extra sdg[r])."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pyreadr

from sdg.cli import main
from sdg.compare import compare
from sdg.formats import detect_module, tabular
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import LabelKey, Options
from sdg.profile import profile, to_yaml
from sdg.report import render

#: A pattern that matches no file name, so no table is private by its name.
NO_PRIVATE_FILES = re.compile(r"(?!x)x")


def write_visits(path: Path) -> None:
    """Write an invented RDS data frame with typed columns and missing cells.

    Args:
        path: The output file.
    """
    rows = 60
    frame = pd.DataFrame({
        "record_id": [f"R{1000 + index}" for index in range(rows)],
        "arm": ["placebo" if index % 3 else "active" for index in range(rows)],
        "crp": [None if index % 10 == 0 else 1.5 + index * 0.25 for index in range(rows)],
        "visits": [float(index % 7) for index in range(rows)],
        "fasting": pd.array([index % 4 == 0 for index in range(rows)], dtype="boolean"),
    })
    pyreadr.write_rds(str(path), frame)


def test_rds_round_trip(tmp_path: Path) -> None:
    """Glimpse, profile and generate an RDS table: the synthetic RDS file reads back with the real structure."""
    path = tmp_path / "trial_export.rds"
    write_visits(path)
    assert detect_module(path) is tabular
    options = Options(private_files=NO_PRIVATE_FILES)
    real = glimpse([path], options)
    assert real.tables[0].format == "rds"
    assert [column.kind for column in real.tables[0].columns] == ["identifier", "categorical", "numeric", "numeric",
                                                                  "boolean"]
    result = profile([path], options)
    assert result.config["datasets"][0]["format"] == "rds"
    config = tmp_path / "config.yaml"
    config.write_text(to_yaml(result), encoding="utf-8")
    generate(config, tmp_path / "out")
    frame = next(iter(pyreadr.read_r(str(tmp_path / "out" / "file_1.rds")).values()))
    assert list(frame.columns) == ["record_id", "arm", "crp", "visits", "fasting"] and len(frame) == 60
    assert frame["crp"].isna().sum() == 6
    synthetic = glimpse([tmp_path / "out" / "file_1.rds"], options)
    assert [item for item in compare(real, synthetic) if item.level == "error"] == []


def test_relabel_copies_an_rds_table(tmp_path: Path) -> None:
    """A relabeled RDS copy has the generic column names and levels."""
    path = tmp_path / "trial_export.rds"
    write_visits(path)
    options = Options(private_files=NO_PRIVATE_FILES, generic_labels=True, key=LabelKey())
    render(glimpse([path], options))
    key = tmp_path / "key.csv"
    options.key.write(key)
    assert main(["relabel", str(key), str(path), "-o", str(tmp_path / "copies")]) == 0
    frame = next(iter(pyreadr.read_r(str(tmp_path / "copies" / "file_1.rds")).values()))
    assert list(frame.columns) == ["identifier_1", "categorical_1", "numeric_1", "numeric_2", "boolean_1"]
    assert set(frame["categorical_1"]) == {"level_1", "level_2"}
