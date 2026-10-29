"""Tests of the config schema, the loader and the error paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from sdg.config import UNION_TAGS, ConfigError, format_location, load_config, parse_config

EXAMPLES = Path(__file__).resolve().parents[1] / "example" / "configs"


def load_example(name: str) -> dict[str, Any]:
    """Read an example config as a plain dictionary, so a test can break it.

    Args:
        name: File name in ``example/configs``.

    Returns:
        The parsed YAML.
    """
    return yaml.safe_load((EXAMPLES / name).read_text(encoding="utf-8"))


def issues(data: Any) -> dict[str, str]:
    """Validate data that must fail and return its issues.

    Args:
        data: A parsed config that is invalid.

    Returns:
        Each issue path mapped to its message.
    """
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    return {issue.path: issue.message for issue in caught.value.issues}


@pytest.mark.parametrize("name", sorted(path.name for path in EXAMPLES.glob("*.yaml")))
def test_examples_are_valid(name: str) -> None:
    """Each committed example validates."""
    load_config(EXAMPLES / name)


def test_unknown_field_is_rejected_with_its_path() -> None:
    """A misspelt field fails instead of being dropped."""
    data = load_example("minimal_tabular.yaml")
    data["datasets"][0]["sheets"][0]["columns"][0]["colour"] = "red"
    assert "datasets[0].sheets[0].columns[0].colour" in issues(data)


def test_decreasing_quantiles_fail() -> None:
    """Quantiles must not decrease."""
    data = load_example("minimal_tabular.yaml")
    data["datasets"][0]["sheets"][0]["columns"][2]["quantiles"] = [3.0, 1.0]
    found = issues(data)
    assert "must not decrease" in found["datasets[0].sheets[0].columns[2].quantiles"]


def test_level_shares_must_sum_to_one() -> None:
    """Level shares far from 1 fail; rounded shares within 0.01 pass."""
    data = load_example("minimal_tabular.yaml")
    levels = data["datasets"][0]["sheets"][0]["columns"][1]["levels"]
    levels["visit_1"] = 0.495
    parse_config(data)
    levels["visit_1"] = 0.3
    assert "sum to" in issues(data)["datasets[0].sheets[0].columns[1].levels"]


def test_column_kind_needs_its_fields() -> None:
    """A numeric column without quantiles fails at the quantiles path."""
    data = load_example("minimal_tabular.yaml")
    del data["datasets"][0]["sheets"][0]["columns"][2]["quantiles"]
    assert "datasets[0].sheets[0].columns[2].quantiles" in issues(data)


def test_csv_has_one_sheet() -> None:
    """A CSV dataset with two sheets fails."""
    data = load_example("model_study.yaml")
    sheet = data["datasets"][0]["sheets"][0]
    data["datasets"][0]["sheets"] = [sheet, {**sheet, "name": "sheet_2"}]
    assert "exactly one sheet" in issues(data)["datasets[0]"]


def test_empty_rows_fit_in_rows() -> None:
    """More empty rows than rows fails."""
    data = load_example("minimal_tabular.yaml")
    data["datasets"][0]["sheets"][0]["empty_rows"] = 500
    assert "exceeds rows" in issues(data)["datasets[0].sheets[0]"]


@pytest.mark.parametrize("file", ["../escape.csv", "/tmp/absolute.csv"])
def test_file_stays_inside_the_output_folder(file: str) -> None:
    """An absolute path or a path with .. fails."""
    data = load_example("minimal_tabular.yaml")
    data["datasets"][0]["file"] = file
    assert "datasets[0].file" in issues(data)


def test_unknown_format_fails() -> None:
    """A format that no module registers fails at the dataset path."""
    data = load_example("minimal_tabular.yaml")
    data["datasets"][0]["format"] = "vcf"
    assert "datasets[0]" in issues(data)


def test_version_must_be_one() -> None:
    """Only schema version 1 exists."""
    data = load_example("minimal_tabular.yaml")
    data["version"] = 2
    assert "version" in issues(data)


def test_effects_need_the_model_mode() -> None:
    """The resample mode rejects effects."""
    data = load_example("model_study.yaml")
    data["mode"] = "resample"
    assert "model mode" in issues(data)["mode"]


def test_model_mode_needs_a_design() -> None:
    """The model mode without a design fails."""
    data = load_example("model_study.yaml")
    del data["design"]
    data["effects"] = []
    assert "design" in issues(data)


@pytest.mark.parametrize(
    ("field", "value", "path"),
    [
        ("dataset", "missing", "effects[0].dataset"),
        ("feature", "feature_99", "effects[0].feature"),
        ("contrast", ["group_a", "group_z"], "effects[0].contrast"),
        ("contrast", ["group_a", "group_a"], "effects[0].contrast"),
    ],
)
def test_effect_references(field: str, value: Any, path: str) -> None:
    """An effect that names a missing dataset, feature or group fails."""
    data = load_example("model_study.yaml")
    data["effects"][0][field] = value
    assert path in issues(data)


def test_duplicate_dataset_names_fail() -> None:
    """Two datasets with one name fail."""
    data = load_example("model_study.yaml")
    data["datasets"].append({**data["datasets"][0], "file": "other.csv"})
    assert "unique" in issues(data)["datasets"]


def test_missing_file(tmp_path: Path) -> None:
    """A missing config file is a file issue, not a crash."""
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    """Broken YAML is a file issue with the parser message."""
    path = tmp_path / "broken.yaml"
    path.write_text("version: [1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_yaml_must_be_a_mapping() -> None:
    """A YAML list is not a config."""
    assert "" in issues([1, 2, 3])


def test_format_location_drops_union_tags() -> None:
    """Tags of tagged unions do not appear in a printed path."""
    location = ("datasets", 0, "excel", "sheets", 1, "columns", 2, "numeric", "quantiles")
    assert format_location(location) == "datasets[0].sheets[1].columns[2].quantiles"
    assert {"csv", "tsv", "excel", "numeric", "identifier"} <= UNION_TAGS
