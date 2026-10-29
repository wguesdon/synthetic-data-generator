"""Tests of sdg init --from-schema, which writes a config from a database schema with no rows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from sdg.cli import main
from sdg.config import load_config
from sdg.generate import generate
from sdg.tables import read_tables

SCHEMA = """
-- A small clinic database. Every name is invented.
CREATE TABLE patients (
    patient_id INTEGER PRIMARY KEY,
    first_name VARCHAR(40) NOT NULL,
    email TEXT,
    birth_date DATE,
    weight_kg DECIMAL(5, 1),
    smoker BOOLEAN
);
CREATE TABLE IF NOT EXISTS "visits" (
    visit_id UUID,
    patient_id INTEGER NOT NULL,
    started_at TIMESTAMP,
    note TEXT,
    PRIMARY KEY (visit_id),
    FOREIGN KEY (patient_id) REFERENCES patients (patient_id)
);
"""


def columns(config: dict, table: int) -> dict[str, dict]:
    """Index the columns of one dataset by name.

    Args:
        config: The config.
        table: Position of the dataset.

    Returns:
        Each column config by name.
    """
    return {column["name"]: column for column in config["datasets"][table]["sheets"][0]["columns"]}


def test_sql_schema_gives_kinds_keys_and_a_link(tmp_path: Path) -> None:
    """CREATE TABLE statements give each column a kind, and a foreign key a link, with no value."""
    schema = tmp_path / "clinic.sql"
    schema.write_text(SCHEMA, encoding="utf-8")
    target = tmp_path / "config.yaml"
    assert main(["init", "--from-schema", str(schema), "--rows", "50", "-o", str(target)]) == 0
    config = yaml.safe_load(target.read_text(encoding="utf-8"))
    patients, visits = columns(config, 0), columns(config, 1)
    assert patients["patient_id"] == {"name": "patient_id", "kind": "identifier", "pattern": "99999"}
    assert patients["first_name"]["provider"] == "first_name" and patients["email"]["provider"] == "email"
    assert patients["birth_date"]["kind"] == "date" and patients["weight_kg"]["decimals"] == 1
    assert patients["smoker"]["kind"] == "boolean"
    assert visits["visit_id"]["pattern"] == "uuid" and visits["started_at"]["date_format"] == "%Y-%m-%d %H:%M:%S"
    assert visits["patient_id"]["link"] == {"dataset": "patients", "column": "patient_id"}
    load_config(target)
    generate(target, tmp_path / "out")
    keys = set(read_tables(tmp_path / "out" / "patients.csv")[0].column(0))
    assert set(read_tables(tmp_path / "out" / "visits.csv")[0].column(1)) <= keys


def test_sqlite_schema_with_no_rows(tmp_path: Path) -> None:
    """A SQLite file with no row gives the same kinds, and its foreign key a link."""
    database = tmp_path / "clinic.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA.replace("UUID", "TEXT"))
    target = tmp_path / "config.yaml"
    assert main(["init", "--from-schema", str(database), "-o", str(target)]) == 0
    config = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [dataset["name"] for dataset in config["datasets"]] == ["patients", "visits"]
    assert columns(config, 1)["patient_id"]["link"] == {"dataset": "patients", "column": "patient_id"}
    assert config["datasets"][0]["sheets"][0]["rows"] == 100


def test_init_needs_formats_or_a_schema(capsys: pytest.CaptureFixture[str]) -> None:
    """With no format and no schema, sdg init fails with a clear message."""
    assert main(["init"]) == 1
    assert "--from-schema" in capsys.readouterr().err
