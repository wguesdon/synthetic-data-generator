"""``sdg validate``: check an output folder against its config and its truth.

Three checks run:

1. Files: each file of the manifest exists and has the recorded SHA-256 hash.
2. Structure: each format module reads its files back and compares them with
   the config, such as the column names and the row count.
3. Effects: in the model mode, each row of ``truth/effects.csv`` is tested
   with Welch's t-test between the two groups at its timepoint, on the log2
   scale. A planted effect is found when the test is significant and the sign
   agrees; on a null dataset, the share of significant rows is the false
   positive rate.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

from sdg.config import Config, load_config
from sdg.formats import module_for_format
from sdg.generate import TRUTH_DIR
from sdg.output import CONFIG_NAME, MANIFEST_NAME, file_sha256

#: Smallest number of samples in each group for a test.
MIN_GROUP = 2


@dataclass
class EffectCheck:
    """The test of one planted effect.

    Attributes:
        dataset: Dataset name.
        feature: Feature name.
        timepoint: Timepoint, or an empty text for every timepoint.
        planted: Planted log2 fold change.
        estimated: Estimated log2 fold change, or None when not checked.
        p_value: Welch p value, or None when not checked.
        found: The test found the effect, or None when not checked.
        note: Why the effect was not checked.
    """

    dataset: str
    feature: str
    timepoint: str
    planted: float
    estimated: float | None = None
    p_value: float | None = None
    found: bool | None = None
    note: str = ""


@dataclass
class Validation:
    """The result of ``sdg validate``.

    Attributes:
        files: Files checked against the manifest.
        issues: File and structure problems.
        effects: The effect tests.
        null: The config is a null dataset.
    """

    files: int = 0
    issues: list[str] = field(default_factory=list)
    effects: list[EffectCheck] = field(default_factory=list)
    null: bool = False


def check_files(out_dir: Path) -> tuple[int, list[str]]:
    """Check each manifest file for presence and hash.

    Args:
        out_dir: The output folder.

    Returns:
        The number of files checked and the problems found.
    """
    manifest_path = out_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return 0, [f"{MANIFEST_NAME}: missing, so this is not an sdg output folder"]
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    issues = []
    for entry in entries:
        path = out_dir / entry["path"]
        if not path.is_file():
            issues.append(f"{entry['path']}: missing")
        elif file_sha256(path) != entry["sha256"]:
            issues.append(f"{entry['path']}: changed since generation")
    return len(entries), issues


def read_truth(out_dir: Path, name: str) -> list[dict[str, str]]:
    """Read one truth table.

    Args:
        out_dir: The output folder.
        name: Table name, such as ``effects``.

    Returns:
        The rows, or an empty list when the table is absent.
    """
    path = out_dir / TRUTH_DIR / f"{name}.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def sample_means(observed: dict[tuple[str, str], list[float]], feature: str, samples: list[str]) -> list[float]:
    """Average the replicate values of each sample for one feature.

    Args:
        observed: Values by sample and feature.
        feature: The feature.
        samples: The samples.

    Returns:
        One mean per sample that has a value.
    """
    return [float(np.mean(observed[(sample, feature)])) for sample in samples if observed.get((sample, feature))]


def assess_effect(row: dict[str, str], observed: dict[tuple[str, str], list[float]],
                groups: dict[str, tuple[str, str]], alpha: float) -> EffectCheck:
    """Test one truth row with Welch's t-test on the log2 scale.

    Args:
        row: The truth row.
        observed: Values by sample and feature.
        groups: Group and timepoint of each present sample.
        alpha: Significance level.

    Returns:
        The effect check.
    """
    check = EffectCheck(row["dataset"], row["feature"], row["timepoint"], float(row["log2_fold_change"]))
    at = [sample for sample, (_, timepoint) in groups.items() if not row["timepoint"] or timepoint == row["timepoint"]]
    moved = sample_means(observed, row["feature"], [sample for sample in at if groups[sample][0] == row["group"]])
    reference = sample_means(observed, row["feature"],
                             [sample for sample in at if groups[sample][0] == row["reference"]])
    if len(moved) < MIN_GROUP or len(reference) < MIN_GROUP:
        check.note = "too few samples with a value"
        return check
    values = np.array(moved + reference)
    if row["scale"] == "log2":
        transform = values
    else:
        transform = np.log2(values + (1.0 if values.min() <= 0 else 0.0))
    first, second = transform[:len(moved)], transform[len(moved):]
    result = stats.ttest_ind(first, second, equal_var=False)
    check.estimated = round(float(first.mean() - second.mean()), 4)
    check.p_value = float(result.pvalue) if math.isfinite(result.pvalue) else 1.0
    agrees = check.planted == 0 or math.copysign(1.0, check.estimated) == math.copysign(1.0, check.planted)
    check.found = check.p_value < alpha and agrees
    return check


def check_effects(config: Config, out_dir: Path, alpha: float) -> list[EffectCheck]:
    """Test each truth row of the effects table.

    Args:
        config: The config.
        out_dir: The output folder.
        alpha: Significance level.

    Returns:
        One check per truth row.
    """
    rows = read_truth(out_dir, "effects")
    groups = {row["sample_id"]: (row["group"], row["timepoint"]) for row in read_truth(out_dir, "samples")
              if row["present"] == "True"}
    datasets = {dataset.name: dataset for dataset in config.datasets}
    cache: dict[str, dict[tuple[str, str], list[float]] | None] = {}
    checks = []
    for row in rows:
        dataset = datasets[row["dataset"]]
        if dataset.name not in cache:
            observer = getattr(module_for_format(dataset.format), "observed", None)
            cache[dataset.name] = observer(dataset, out_dir) if observer else None
        observed = cache[dataset.name]
        if observed is None:
            checks.append(EffectCheck(row["dataset"], row["feature"], row["timepoint"],
                                      float(row["log2_fold_change"]), note=f"{dataset.format} values are not read back"))
        else:
            checks.append(assess_effect(row, observed, groups, alpha))
    return checks


def validate(out_dir: Path, alpha: float = 0.05) -> Validation:
    """Validate an output folder.

    Args:
        out_dir: The output folder.
        alpha: Significance level of the effect tests.

    Returns:
        The validation result.
    """
    result = Validation()
    result.files, result.issues = check_files(out_dir)
    config_path = out_dir / CONFIG_NAME
    if not config_path.is_file():
        result.issues.append(f"{CONFIG_NAME}: missing")
        return result
    config = load_config(config_path)
    result.null = config.null
    for dataset in config.datasets:
        checker = getattr(module_for_format(dataset.format), "check_output", None)
        if checker:
            result.issues += checker(dataset, config, out_dir)
    if config.mode == "model":
        result.effects = check_effects(config, out_dir, alpha)
    return result


def summary(result: Validation) -> dict[str, Any]:
    """Summarize a validation for the JSON output.

    Args:
        result: The validation.

    Returns:
        The counts, the issues and the effect checks.
    """
    tested = [check for check in result.effects if check.found is not None]
    planted = [check for check in tested if check.planted != 0]
    return {"files": result.files, "issues": result.issues, "null": result.null,
            "effects_tested": len(tested), "effects_found": sum(check.found for check in planted),
            "effects_planted": len(planted),
            "false_positive_share": (round(sum(check.found for check in tested) / len(tested), 4)
                                     if result.null and tested else None),
            "effects": [vars(check) for check in result.effects]}
