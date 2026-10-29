"""Tests of the pytest plugin of sdg, which gives a test its synthetic data."""

from __future__ import annotations

from pathlib import Path

from sdg.tables import read_tables

EXAMPLE = Path(__file__).resolve().parents[1] / "example" / "configs" / "minimal_tabular.yaml"


def test_fixture_generates_a_config(sdg_data) -> None:  # noqa: ANN001 - a pytest fixture
    """The fixture from the entry point generates the example into a temporary folder."""
    out = sdg_data(EXAMPLE)
    assert (out / "manifest.json").is_file()
    assert len(read_tables(out / "synthetic_clinical.xlsx")[0].rows) == 120


def test_fixture_gives_a_new_folder_on_each_call(sdg_data) -> None:  # noqa: ANN001 - a pytest fixture
    """Two calls write two folders, so two tests never share an output."""
    assert sdg_data(EXAMPLE) != sdg_data(EXAMPLE, name="again")
