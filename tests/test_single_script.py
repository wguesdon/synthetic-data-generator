"""Tests of the single script ``single_script/sdg_secure.py``."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sdg.cli import main
from sdg.generate import generate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "single_script" / "sdg_secure.py"

#: Python code that imports the single script and fails inside a bundled module.
FAILING_IMPORT = """
import pandas as pd

import sdg_secure
import sdg
from sdg import describe

print(sdg.__spec__.origin)
describe.groups(pd.DataFrame({"IL6": [1.0, 2.0]}), group="nope")
"""


def run_python(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the test Python with the single script folder on the import path.

    Args:
        arguments: The arguments after the interpreter.
        cwd: The working folder.

    Returns:
        The completed process, with its text output.
    """
    environment = {**os.environ, "PYTHONPATH": str(SCRIPT.parent), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, *arguments], cwd=cwd, env=environment,
                          capture_output=True, text=True, check=False)


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    """Generate the minimal Excel example.

    Args:
        tmp_path: The pytest temporary folder.

    Returns:
        The workbook.
    """
    generate(ROOT / "example" / "configs" / "minimal_tabular.yaml", tmp_path / "data")
    return tmp_path / "data" / "synthetic_clinical.xlsx"


def test_single_script_is_up_to_date() -> None:
    """The committed script holds the current source of the package."""
    completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_single_script.py"), "--check"],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("arguments", [["glimpse"], ["glimpse", "--private", "^$"], ["describe", "design"],
                                       ["describe", "groups", "--private", "^$", "--group", "responder"], ["report"]])
def test_single_script_prints_the_package_reports(workbook: Path, tmp_path: Path, arguments: list[str],
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """Each command of the single script prints the report of the installed package."""
    assert main([*arguments, str(workbook)]) == 0
    expected = capsys.readouterr().out
    completed = run_python([str(SCRIPT), *arguments, str(workbook)], tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == expected


def test_single_script_loads_sdg_from_its_table(tmp_path: Path) -> None:
    """An import of the script loads sdg from the table, and a traceback shows the bundled lines."""
    completed = run_python(["-c", FAILING_IMPORT], tmp_path)
    assert completed.stdout == "<sdg_secure>/sdg/__init__.py\n"
    assert 'File "<sdg_secure>/sdg/describe.py"' in completed.stderr
    assert 'raise ValueError(f"{role}: no column named {name!r}")' in completed.stderr
    assert completed.stderr.rstrip().endswith("ValueError: group: no column named 'nope'")
