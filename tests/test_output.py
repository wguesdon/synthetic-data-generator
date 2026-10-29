"""Tests of the config copy, the manifest and the notice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sdg.config import load_config
from sdg.output import copy_config, write_manifest, write_notice
from sdg.version import SDG_VERSION

EXAMPLE = Path(__file__).resolve().parents[1] / "example" / "configs" / "minimal_tabular.yaml"


def test_manifest_lists_each_file_with_its_hash(tmp_path: Path) -> None:
    """Each file appears with its SHA-256 hash, with the version and the seed."""
    config = load_config(EXAMPLE)
    data = tmp_path / "synthetic_clinical.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")
    manifest = json.loads(write_manifest(tmp_path, config, [data]).read_text(encoding="utf-8"))
    assert manifest["version"] == SDG_VERSION
    assert manifest["seed"] == config.seed
    assert manifest["files"] == [{"path": "synthetic_clinical.csv",
                                  "sha256": hashlib.sha256(b"a,b\n1,2\n").hexdigest()}]


def test_manifest_is_reproducible(tmp_path: Path) -> None:
    """Two writes of the same files give the same manifest bytes."""
    config = load_config(EXAMPLE)
    data = tmp_path / "data.csv"
    data.write_text("x\n", encoding="utf-8")
    first = write_manifest(tmp_path, config, [data]).read_bytes()
    assert write_manifest(tmp_path, config, [data]).read_bytes() == first


def test_config_copy_keeps_comments(tmp_path: Path) -> None:
    """The copy is byte for byte, so the assumption comments survive."""
    copy = copy_config(EXAMPLE, tmp_path)
    assert copy.read_bytes() == EXAMPLE.read_bytes()


def test_notice_says_synthetic(tmp_path: Path) -> None:
    """The notice tells a reader that the values have no meaning."""
    text = write_notice(tmp_path).read_text(encoding="utf-8")
    assert text.startswith("# Synthetic data")
    assert "no meaning" in text
