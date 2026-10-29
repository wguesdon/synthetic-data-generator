"""Tests of the processed microarray format."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sdg.compare import compare
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.matrix import read_matrix
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import render
from sdg.templates import init_config

OPTIONS = Options(format="microarray")


def generate_text(text: str, tmp_path: Path, name: str) -> Path:
    """Write a config text and generate it.

    Args:
        text: The YAML config.
        tmp_path: The pytest temporary folder.
        name: Output folder name.

    Returns:
        The output folder.
    """
    path = tmp_path / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    generate(path, tmp_path / name)
    return tmp_path / name


def test_model_effect_adds_the_log2_fold_change(tmp_path: Path) -> None:
    """An effect of fold change 4 raises a probe by 2 on the log2 scale."""
    data = yaml.safe_load(init_config(["microarray"], "model"))
    data["design"]["subjects"]["groups"] = {"group_a": 40, "group_b": 40}
    data["design"]["timepoints"] = ["baseline"]
    data["effects"][0]["fold_change"] = 4.0
    data["datasets"][0]["array_shift_sd"] = 0.0
    matrix = read_matrix(generate_text(yaml.safe_dump(data), tmp_path, "out") / "dataset_1.tsv")
    shift = matrix.values[0, :40].mean() - matrix.values[0, 40:].mean()
    assert shift == pytest.approx(2.0, abs=0.3)


def test_round_trip_and_fit(tmp_path: Path) -> None:
    """Profile and generate keep the structure; the fit is close to the config."""
    data = yaml.safe_load(init_config(["microarray"]))
    data["datasets"][0]["samples"] = 30
    first = generate_text(yaml.safe_dump(data), tmp_path, "first") / "dataset_1.tsv"
    result = profile([first], Options(format="microarray", extra={"platform": "GPL570"}))
    fitted = result.config["datasets"][0]
    assert fitted["platform"] == "GPL570" and fitted["decimals"] == 3
    assert fitted["mean_quantiles"][5] == pytest.approx(6.5, abs=0.4)
    second = generate_text(to_yaml(result), tmp_path, "second") / "file_1.tsv"
    assert [item for item in compare(glimpse([first], OPTIONS), glimpse([second], OPTIONS))
            if item.level == "error"] == []


def test_sample_names_stay_private(tmp_path: Path) -> None:
    """A glimpse shows the shapes of the GSM names, never the names."""
    path = tmp_path / "series.tsv"
    names = [f"GSM10{index:05d}" for index in range(12)]
    rows = ["ID_REF\t" + "\t".join(names)] + [f"{probe}_at\t" + "\t".join(f"{6 + index * 0.1:.3f}" for index in range(12))
                                              for probe in range(20)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    text = render(glimpse([path], OPTIONS))
    assert "GSM10" not in text and "'AAA9999999' 12" in text
    assert read_matrix(path).values.shape == (20, 12)
