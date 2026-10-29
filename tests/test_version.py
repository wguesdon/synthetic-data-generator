"""Tests that the version has one value everywhere."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from sdg.version import SDG_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_version_file_matches_package() -> None:
    """The root VERSION file and sdg/version.py agree."""
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == SDG_VERSION


def test_r_package_matches_package() -> None:
    """The R package sdgr has the same version."""
    description = (ROOT / "r" / "sdgr" / "DESCRIPTION").read_text(encoding="utf-8")
    assert f"Version: {SDG_VERSION}\n" in description


def test_installed_metadata_matches_package() -> None:
    """The installed package reports the same version through hatchling."""
    assert version("sdg") == SDG_VERSION
