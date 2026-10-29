"""Tests of the sdg command line."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sdg.cli import main
from sdg.version import SDG_VERSION

EXAMPLE = Path(__file__).resolve().parents[1] / "example" / "configs" / "minimal_tabular.yaml"


def test_schema_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    """The schema is JSON and names the top-level sections."""
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert {"version", "seed", "datasets"} <= set(schema["properties"])
    assert "TabularDataset" in schema["$defs"]


def test_schema_writes_a_file(tmp_path: Path) -> None:
    """The -o option writes the schema to a file."""
    target = tmp_path / "schema.json"
    assert main(["schema", "-o", str(target)]) == 0
    assert "properties" in json.loads(target.read_text(encoding="utf-8"))


def test_check_valid(capsys: pytest.CaptureFixture[str]) -> None:
    """A valid config exits 0 and prints its summary."""
    assert main(["check", str(EXAMPLE)]) == 0
    assert capsys.readouterr().out.startswith("Valid config: 1 dataset(s)")


def test_check_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """The JSON summary gives the counts of each dataset."""
    assert main(["check", str(EXAMPLE), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True
    assert result["summary"]["datasets"][0]["columns"] == 6


def test_check_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An invalid config exits 1 and gives each issue with its path."""
    broken = tmp_path / "broken.yaml"
    broken.write_text(EXAMPLE.read_text(encoding="utf-8").replace("true_share: 0.35", "true_share: 1.5"),
                      encoding="utf-8")
    assert main(["check", str(broken), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is False
    assert result["issues"][0]["path"] == "datasets[0].sheets[0].columns[5].true_share"


def test_version_flag() -> None:
    """The module runs as a program and prints the version."""
    completed = subprocess.run([sys.executable, "-m", "sdg.cli", "--version"],
                               capture_output=True, text=True, check=True)
    assert completed.stdout.strip() == f"sdg {SDG_VERSION}"
