"""Tests of the model mode: the design, the simulation and the tabular design sheets."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from scipy import stats

from sdg.config import ConfigError, parse_config
from sdg.design import all_samples, design_rows, subjects
from sdg.generate import generate
from sdg.modes.model import Feature, simulate

#: A right-skewed feature, like a cytokine concentration.
SKEWED = [1.0, 1.6, 2.1, 2.6, 3.1, 3.7, 4.4, 5.3, 6.6, 8.8, 14.0]


def model_config(groups: dict[str, int], timepoints: list[str], effects: list[dict[str, Any]] | None = None,
                 **extra: Any) -> dict[str, Any]:
    """Build a model-mode config with one wide CSV dataset.

    Args:
        groups: Each group and its subject count.
        timepoints: Timepoint labels.
        effects: The effects.
        **extra: Other top-level sections, such as ``artefacts`` or ``null``.

    Returns:
        The config as plain data.
    """
    columns = [{"name": "subject", "kind": "design", "field": "subject"},
               {"name": "group", "kind": "design", "field": "group"},
               {"name": "visit", "kind": "design", "field": "timepoint"},
               {"name": "f1", "kind": "numeric", "quantiles": SKEWED, "decimals": 3}]
    config = {"version": 1, "seed": 7, "mode": "model",
              "design": {"subjects": {"groups": groups}, "timepoints": timepoints},
              "datasets": [{"name": "assay", "format": "csv", "file": "assay.csv",
                            "sheets": [{"columns": columns}]}],
              "effects": effects or []}
    config.update(extra)
    return config


def write_and_generate(config: dict[str, Any], tmp_path: Path) -> Path:
    """Write a config and generate it.

    Args:
        config: The config as plain data.
        tmp_path: The pytest temporary folder.

    Returns:
        The output folder.
    """
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    generate(path, tmp_path / "out")
    return tmp_path / "out"


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV output as dictionaries.

    Args:
        path: The CSV file.

    Returns:
        One dictionary per row.
    """
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_subjects_and_samples() -> None:
    """Subject identifiers count up; the first timepoint is never absent."""
    config = parse_config(model_config({"a": 3, "b": 2}, ["t1", "t2", "t3"]))
    assert subjects(config.design)[:2] == [("SUBJ-001", "a"), ("SUBJ-002", "a")]
    data = model_config({"a": 40, "b": 40}, ["t1", "t2", "t3"])
    data["design"]["missing_samples"] = 0.3
    samples = all_samples(parse_config(data))
    assert all(sample.present for sample in samples if sample.timepoint == "t1")
    absent = sum(not sample.present for sample in samples) / (80 * 2)
    assert 0.2 < absent < 0.4
    assert samples[1].sample_id == "SUBJ-001_t2"


def test_marginal_follows_the_quantiles() -> None:
    """With no effect, the simulated values follow the configured quantiles."""
    config = parse_config(model_config({"a": 1000}, []))
    values = simulate(config, "assay", [Feature("f1", SKEWED)]).values[:, 0]
    observed = np.quantile(values, np.linspace(0, 1, 11))
    assert np.allclose(observed[1:-1], SKEWED[1:-1], rtol=0.1)


@pytest.mark.parametrize(("scale", "check"), [("linear", "ratio"), ("log2", "difference")])
def test_effect_size(scale: str, check: str) -> None:
    """A fold change of 2 doubles the mean (linear) or adds 1 (log2)."""
    effect = [{"dataset": "assay", "feature": "f1", "contrast": ["a", "b"], "fold_change": 2.0}]
    config = parse_config(model_config({"a": 1500, "b": 1500}, [], effect))
    simulation = simulate(config, "assay", [Feature("f1", SKEWED, scale)])
    groups = np.array([row.sample.group for row in simulation.rows])
    moved, reference = simulation.values[groups == "a", 0], simulation.values[groups == "b", 0]
    if check == "ratio":
        assert moved.mean() / reference.mean() == pytest.approx(2.0, rel=0.08)
    else:
        assert moved.mean() - reference.mean() == pytest.approx(1.0, abs=0.15)
    assert simulation.effects[0]["fold_change"] == 2.0


def test_effect_applies_at_its_timepoints_only() -> None:
    """An effect at t2 leaves t1 alone."""
    effect = [{"dataset": "assay", "feature": "f1", "contrast": ["a", "b"], "fold_change": 3.0,
               "timepoints": ["t2"]}]
    plain = simulate(parse_config(model_config({"a": 50, "b": 50}, ["t1", "t2"])), "assay", [Feature("f1", SKEWED)])
    moved = simulate(parse_config(model_config({"a": 50, "b": 50}, ["t1", "t2"], effect)), "assay",
                     [Feature("f1", SKEWED)])
    at_t1 = np.array([row.sample.timepoint == "t1" for row in moved.rows])
    np.testing.assert_array_equal(plain.values[at_t1], moved.values[at_t1])
    assert [row["timepoint"] for row in moved.effects] == ["t2"]


def test_null_dataset_plants_nothing() -> None:
    """In a null dataset each effect has a fold change of 1."""
    effect = [{"dataset": "assay", "feature": "f1", "contrast": ["a", "b"], "fold_change": 4.0}]
    data = model_config({"a": 20, "b": 20}, [], effect, null=True)
    simulation = simulate(parse_config(data), "assay", [Feature("f1", SKEWED)])
    plain = simulate(parse_config(model_config({"a": 20, "b": 20}, [])), "assay", [Feature("f1", SKEWED)])
    np.testing.assert_array_equal(simulation.values, plain.values)
    assert simulation.effects[0]["fold_change"] == 1.0 and simulation.effects[0]["planned_fold_change"] == 4.0


def welch_p_values(simulation: Any) -> np.ndarray:
    """Test each feature between groups a and b with Welch's t-test on the log scale.

    Args:
        simulation: A simulation with groups a and b.

    Returns:
        One p value per feature.
    """
    groups = np.array([row.sample.group for row in simulation.rows])
    logged = np.log2(simulation.values)
    return stats.ttest_ind(logged[groups == "a"], logged[groups == "b"], equal_var=False).pvalue


def test_null_false_positive_rate() -> None:
    """Criterion 7: on a null dataset, the share of p < 0.05 is near 0.05."""
    features = [Feature(f"f{index}", SKEWED) for index in range(400)]
    config = parse_config(model_config({"a": 15, "b": 15}, []))
    share = (welch_p_values(simulate(config, "assay", features)) < 0.05).mean()
    assert 0.02 < share < 0.09


def test_planted_effects_are_found() -> None:
    """Criterion 6: a standard test finds a planted fold change of 2."""
    features = [Feature(f"f{index}", SKEWED) for index in range(20)]
    effects = [{"dataset": "assay", "feature": f"f{index}", "contrast": ["a", "b"], "fold_change": 2.0}
               for index in range(20)]
    data = model_config({"a": 20, "b": 20}, [], effects)
    data["datasets"][0]["sheets"][0]["columns"] += [{"name": f"f{index}", "kind": "numeric", "quantiles": SKEWED}
                                                    for index in range(20)]
    data["datasets"][0]["sheets"][0]["columns"].pop(3)
    p_values = welch_p_values(simulate(parse_config(data), "assay", features))
    assert (p_values < 0.05).sum() >= 19


def test_replicates_and_censoring(tmp_path: Path) -> None:
    """Replicates repeat each sample with the configured spread; censoring uses the token."""
    data = model_config({"a": 150, "b": 150}, [],
                        artefacts={"lower_limit_quantile": 0.1, "censor_token": "<LLOQ",
                                   "replicates": {"n": 3, "cv_pct": 10.0}})
    data["datasets"][0]["sheets"][0]["columns"].append({"name": "well", "kind": "design", "field": "replicate"})
    rows = read_csv(write_and_generate(data, tmp_path) / "assay.csv")
    assert len(rows) == 900 and {row["well"] for row in rows} == {"1", "2", "3"}
    assert 0.07 < sum(row["f1"] == "<LLOQ" for row in rows) / 900 < 0.13
    simulation = simulate(parse_config(data), "assay", [Feature("f1", SKEWED)])
    by_sample = simulation.values[:, 0].reshape(-1, 3)
    cv = (by_sample.std(axis=1, ddof=1) / by_sample.mean(axis=1)).mean()
    assert cv == pytest.approx(0.094, abs=0.02)


def test_long_layout(tmp_path: Path) -> None:
    """A long sheet has one row per sample and feature."""
    data = model_config({"a": 4, "b": 4}, ["t1", "t2"])
    data["datasets"][0]["sheets"][0] = {"layout": "long", "features": [
        {"name": "IL6", "quantiles": [0.5, 2.0, 9.0], "scale": "log2", "decimals": 2},
        {"name": "TNF", "quantiles": [1.0, 3.0]}], "columns": [
        {"name": "Patient", "kind": "design", "field": "subject"},
        {"name": "Visit", "kind": "design", "field": "timepoint"},
        {"name": "Analyte", "kind": "design", "field": "feature"},
        {"name": "Value", "kind": "design", "field": "value"}]}
    rows = read_csv(write_and_generate(data, tmp_path) / "assay.csv")
    assert len(rows) == 8 * 2 * 2
    assert [row["Analyte"] for row in rows[:4]] == ["IL6", "TNF", "IL6", "TNF"]
    assert rows[0]["Patient"] == "SUBJ-001" and rows[0]["Visit"] == "t1"


def test_datasets_share_the_design(tmp_path: Path) -> None:
    """Two datasets lack the same samples, and the truth lists them."""
    data = model_config({"a": 10, "b": 10}, ["t1", "t2", "t3"])
    data["design"]["missing_samples"] = 0.3
    second = yaml.safe_load(yaml.safe_dump(data["datasets"][0]))
    second.update({"name": "other", "file": "other.csv"})
    data["datasets"].append(second)
    out = write_and_generate(data, tmp_path)
    first_keys = {(row["subject"], row["visit"]) for row in read_csv(out / "assay.csv")}
    second_keys = {(row["subject"], row["visit"]) for row in read_csv(out / "other.csv")}
    assert first_keys == second_keys
    truth = read_csv(out / "truth" / "samples.csv")
    assert len(truth) == 60 and sum(row["present"] == "True" for row in truth) == len(first_keys)


@pytest.mark.parametrize(("change", "message"), [
    ("resample", "design columns need the model mode"),
    ("long_without_feature", "exactly one design column of field 'feature'"),
    ("effect_on_plain_sheet", "has no feature"),
])
def test_design_validation(change: str, message: str) -> None:
    """Design sheets need the model mode, and a long sheet needs its fields."""
    data = model_config({"a": 4, "b": 4}, ["t1"])
    sheet = data["datasets"][0]["sheets"][0]
    if change == "resample":
        data["mode"] = "resample"
    elif change == "long_without_feature":
        sheet.update({"layout": "long", "features": [{"name": "x", "quantiles": [0, 1]}]})
    else:
        sheet["columns"] = [{"name": "f1", "kind": "numeric", "quantiles": SKEWED}]
        sheet["rows"] = 10
        data["effects"] = [{"dataset": "assay", "feature": "f1", "contrast": ["a", "b"], "fold_change": 2.0}]
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_design_rows_expand_replicates() -> None:
    """Each present sample appears once per replicate."""
    config = parse_config(model_config({"a": 2}, ["t1"]))
    rows = design_rows(config, replicates=3)
    assert [row.replicate for row in rows] == [1, 2, 3, 1, 2, 3]
