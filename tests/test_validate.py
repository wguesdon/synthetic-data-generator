"""Tests of ``sdg validate``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from sdg.cli import main
from sdg.generate import generate
from sdg.validate import validate

EXAMPLES = Path(__file__).resolve().parents[1] / "example" / "configs"


def generated(name: str, tmp_path: Path, edit: dict | None = None) -> Path:
    """Generate one example, with optional top-level changes.

    Args:
        name: Example name.
        tmp_path: The pytest temporary folder.
        edit: Top-level config changes.

    Returns:
        The output folder.
    """
    data = yaml.safe_load((EXAMPLES / f"{name}.yaml").read_text(encoding="utf-8"))
    data.update(edit or {})
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    generate(path, tmp_path / name)
    return tmp_path / name


def test_clean_output_finds_its_effects(tmp_path: Path) -> None:
    """A fresh output matches its config, and the planted probes are found."""
    result = validate(generated("microarray_series", tmp_path))
    assert result.issues == [] and result.files == 5
    assert [check.found for check in result.effects] == [True, True, True]


def test_changed_file_is_a_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file edited after generation fails the hash check."""
    out = generated("plate_export", tmp_path)
    data = out / "plate_export.csv"
    data.write_text(data.read_text(encoding="utf-8").replace("Sample ID", "Sample", 1), encoding="utf-8")
    assert main(["validate", str(out)]) == 1
    text = capsys.readouterr().out
    assert "changed since generation" in text and "header" in text


def test_missing_file_and_foreign_folder(tmp_path: Path) -> None:
    """A deleted file and a folder without a manifest are problems."""
    out = generated("rnaseq_fastq", tmp_path)
    (out / "fastq" / "S1_R2_001.fastq.gz").unlink()
    issues = validate(out).issues
    assert any("missing" in issue for issue in issues) and any("FASTQ files" in issue for issue in issues)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    assert "not an sdg output folder" in validate(foreign).issues[0]


def test_null_dataset_gives_the_false_positive_share(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """On a null dataset, the planted effects are 1 and the share of significant tests is near alpha."""
    data = yaml.safe_load((EXAMPLES / "microarray_series.yaml").read_text(encoding="utf-8"))
    data["null"] = True
    data["effects"] = [{"dataset": "expression", "feature": f"probe_{index}", "contrast": ["responder", "non_responder"],
                        "fold_change": 2.0} for index in range(1, 201)]
    path = tmp_path / "null.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    generate(path, tmp_path / "out")
    assert main(["validate", str(tmp_path / "out"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["null"] is True and result["effects_tested"] == 200
    assert result["false_positive_share"] < 0.12


def test_require_effects(tmp_path: Path) -> None:
    """With --require-effects, a planted effect that is not found fails."""
    out = generated("assay_long", tmp_path)
    assert main(["validate", str(out)]) == 0
    assert main(["validate", str(out), "--require-effects"]) == 1


def test_fcs_effect_is_found(tmp_path: Path) -> None:
    """The population frequencies are read back, and the planted FCS effect is found."""
    result = validate(generated("fcs_panel", tmp_path))
    assert result.issues == []
    planted = [check for check in result.effects if check.planted != 0]
    assert [check.feature for check in planted] == ["cd8_t"]
    assert planted[0].found is True and abs(planted[0].estimated - 1.0) < 0.3


def test_fcs_null_dataset_finds_no_effect(tmp_path: Path) -> None:
    """On a null FCS dataset, the frequency of the target population does not move."""
    result = validate(generated("fcs_panel", tmp_path, {"null": True}))
    assert [check.found for check in result.effects] == [False]
