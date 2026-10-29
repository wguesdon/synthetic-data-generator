"""Turn a resample config and a groups report into a model-mode config.

This script does the step that the agent does in the sdg workflow: it reads the
``sdg describe groups`` report and plants each ratio of medians as an effect.
The comparison runs it on the penguin table, with the private-column rule
switched off, to test whether sdg keeps a group difference.

* The group column becomes a ``design`` column of field ``group``.
* The design has one subject per row, with the row count of each group.
* Each numeric feature gets one effect per group against the reference, with
  the ratio of the report. A feature with no ratio, such as a feature with
  negative values, gets no effect.
* A sheet with design columns takes no ``correlations``, so the field goes.

Usage:
    uv run python scripts/comparison/make_model_config.py CONFIG REPORT GROUP OUTPUT
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

FEATURE = re.compile(r"^\$ '(?P<name>[^']+)' <numeric> (?P<body>.*)$")
RATIO = re.compile(r"'(?P<group>[^']+)' vs '(?P<reference>[^']+)': ratio (?P<ratio>[0-9.]+)")


def read_ratios(report: str) -> dict[str, list[tuple[str, str, float]]]:
    """Read the ratio of each group against the reference, for each feature.

    Args:
        report: The text of an ``sdg describe groups`` report.

    Returns:
        Each feature mapped to its (group, reference, ratio) items.
    """
    ratios: dict[str, list[tuple[str, str, float]]] = {}
    for line in report.splitlines():
        match = FEATURE.match(line)
        if match:
            ratios[match["name"]] = [(item["group"], item["reference"], float(item["ratio"]))
                                     for item in RATIO.finditer(match["body"])]
    return ratios


def model_config(config: dict, ratios: dict[str, list[tuple[str, str, float]]], group: str) -> dict:
    """Change a one-sheet resample config into a model-mode config.

    Args:
        config: The resample config of one table.
        ratios: The ratios of :func:`read_ratios`.
        group: The name of the group column.

    Returns:
        The model-mode config.
    """
    dataset = config["datasets"][0]
    sheet = dataset["sheets"][0]
    rows = sheet.pop("rows")
    sheet.pop("correlations", None)
    column = next(column for column in sheet["columns"] if column["name"] == group)
    shares = column["levels"]
    counts = {level: round(share * rows) for level, share in shares.items()}
    column.clear()
    column.update({"name": group, "kind": "design", "field": "group"})
    config["mode"] = "model"
    config["design"] = {"subjects": {"prefix": "SUBJ-", "groups": counts}, "timepoints": []}
    config["effects"] = [
        {"dataset": dataset["name"], "feature": feature, "contrast": [moved, reference], "fold_change": ratio}
        for feature, items in ratios.items() for moved, reference, ratio in items if ratio != 1.0
    ]
    return config


def main() -> int:
    """Write the model-mode config.

    Returns:
        The exit code.
    """
    config_path, report_path, group, output = sys.argv[1:5]
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    ratios = read_ratios(Path(report_path).read_text(encoding="utf-8"))
    Path(output).write_text(yaml.safe_dump(model_config(config, ratios, group), sort_keys=False),
                            encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
