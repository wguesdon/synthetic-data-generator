"""Measure the privacy and the fidelity of each synthetic table of the comparison.

The script reads the real public table of each case and the synthetic table of
each tool and variant under a run folder. Columns pair by position, because a
generic variant renames them. The measures do not import sdg, so they are the
same for each tool. One extra privacy measure calls ``sdg audit`` on each file
that leaves the secure machine, and its rows say so.

Usage:
    uv run python scripts/comparison/measure.py RUN_DIR

Writes ``RUN_DIR/metrics.csv`` (one row per variant, case and metric) and
``RUN_DIR/summary.md`` (one table per metric).
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

VARIANTS = ["sdg_real", "sdg_generic", "sdg_rule_off", "sdg_model", "fakedatar_low", "fakedatar_medium",
            "fakedatar_high", "faker"]
GENERIC_VARIANTS = {"sdg_generic", "fakedatar_medium", "fakedatar_high"}
MISSING = {"", "NA", "N/A", "NaN", "nan", "NULL", "null", "None", "#N/A"}
CATEGORY_LIMIT = 20
MIN_VALUES = 10
MIN_PAIRS = 20
RARE_DIGITS = 5

INT = re.compile(r"[-+]?\d+")
FLOAT = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z?")
LETTER = re.compile(r"[A-Za-z]")

#: Identifier columns of each case. A number in such a column is a key, not a
#: measurement, so the distribution and correlation measures leave it out.
IDENTIFIERS = {
    "penguins_raw": {"Sample Number", "Individual ID"},
    "mimic_patients": {"subject_id"},
    "mimic_admissions": {"subject_id", "hadm_id", "admit_provider_id"},
    "titanic": {"PassengerId"},
}

#: A numeric outcome and a group column for each case with a known group difference.
GROUP_CHECKS = {
    "penguins_raw": ("Body Mass (g)", "Species"),
    "heart_failure": ("time", "DEATH_EVENT"),
    "titanic": ("Survived", "Sex"),
}

#: Pairs of date columns where the first date comes before or on the second.
ORDER_CHECKS = {
    "mimic_admissions": [("admittime", "dischtime"), ("edregtime", "edouttime")],
}

#: A child table, its key column, the parent table and its key column.
JOIN_CHECK = ("mimic_admissions", "subject_id", "mimic_patients", "subject_id")


@dataclass(frozen=True)
class Metric:
    """One measure of one synthetic table.

    Attributes:
        variant: The tool and its variant, such as ``fakedatar_high``.
        case: The case name.
        metric: The measure name.
        value: The value, or NaN when the measure does not apply.
        detail: A short note, such as the counts behind a share.
    """

    variant: str
    case: str
    metric: str
    value: float
    detail: str = ""


def read_cases(path: Path) -> list[tuple[str, Path]]:
    """Read the case list.

    Args:
        path: The tab-separated case file.

    Returns:
        Each case name with its real file.
    """
    with path.open(newline="") as handle:
        return [(row["case"], Path(row["file"])) for row in csv.DictReader(handle, delimiter="\t")]


def read_text_table(path: Path) -> pd.DataFrame:
    """Read a CSV file with every cell as text.

    Args:
        path: The CSV file.

    Returns:
        The table, with an empty cell as an empty text.
    """
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def filled(column: pd.Series) -> list[str]:
    """Return the cells of a column that are not missing.

    Args:
        column: A text column.

    Returns:
        The filled cells, stripped.
    """
    return [cell.strip() for cell in column.tolist() if cell.strip() not in MISSING]


def canon(cell: str) -> str:
    """Give a cell one text form, so that ``1`` and ``1.0`` compare equal.

    Args:
        cell: A text cell.

    Returns:
        The canonical text.
    """
    cell = cell.strip()
    if cell in MISSING:
        return ""
    if FLOAT.fullmatch(cell):
        return repr(float(cell))
    return cell


def column_kind(values: list[str]) -> str:
    """Classify the filled cells of a column, as code that reads the file sees them.

    Args:
        values: Filled cells.

    Returns:
        ``empty``, ``int``, ``float``, ``bool``, ``datetime``, ``date`` or ``text``.
    """
    if not values:
        return "empty"
    if all(INT.fullmatch(value) for value in values):
        return "int"
    if all(FLOAT.fullmatch(value) for value in values):
        return "float"
    if all(value.lower() in {"true", "false"} for value in values):
        return "bool"
    if all(DATETIME.fullmatch(value) for value in values):
        return "datetime"
    if all(DATE.fullmatch(value) for value in values):
        return "date"
    return "text"


def shape(value: str) -> str:
    """Return the shape of a value: a digit becomes 9 and a letter becomes A or a.

    Args:
        value: A cell.

    Returns:
        The shape.
    """
    return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", value)))


def significant_digits(value: str) -> int:
    """Count the significant digits of a number cell.

    Args:
        value: A number as text.

    Returns:
        The count. Trailing zeros of an integer do not count.
    """
    mantissa = re.split(r"[eE]", value.lstrip("+-"))[0]
    digits = mantissa.replace(".", "").lstrip("0")
    if "." not in mantissa:
        digits = digits.rstrip("0")
    return len(digits)


def rare_values(values: list[str]) -> tuple[set[str], set[float]]:
    """Find the values that occur once in a real column.

    A value that occurs once points to one record. Short values and numbers
    with less than ``RARE_DIGITS`` significant digits are left out, because a
    generator can draw them by chance.

    Args:
        values: Filled cells of a real column.

    Returns:
        The rare text values and the rare numbers.
    """
    singles = [value for value, count in Counter(values).items() if count == 1 and len(value) >= 3]
    texts = {value for value in singles if not FLOAT.fullmatch(value)}
    numbers = {float(value) for value in singles
               if FLOAT.fullmatch(value) and significant_digits(value) >= RARE_DIGITS}
    return texts, numbers


def privacy_metrics(variant: str, case: str, real: pd.DataFrame, synthetic: pd.DataFrame) -> list[Metric]:
    """Measure the real values in a synthetic table.

    Args:
        variant: The variant name.
        case: The case name.
        real: The real table.
        synthetic: The synthetic table, with columns in the real order.

    Returns:
        The shares of rare text values, rare numbers and whole rows that the
        synthetic table copies.
    """
    text_total = text_found = number_total = number_found = 0
    examples: list[str] = []
    for index in range(real.shape[1]):
        texts, numbers = rare_values(filled(real.iloc[:, index]))
        synthetic_cells = filled(synthetic.iloc[:, index])
        synthetic_texts = set(synthetic_cells)
        synthetic_numbers = {float(cell) for cell in synthetic_cells if FLOAT.fullmatch(cell)}
        copied = texts & synthetic_texts
        text_total += len(texts)
        text_found += len(copied)
        number_total += len(numbers)
        number_found += len(numbers & synthetic_numbers)
        examples += sorted(copied)[:1]
    real_rows = {tuple(canon(cell) for cell in row) for row in real.itertuples(index=False)}
    copies = sum(tuple(canon(cell) for cell in row) in real_rows for row in synthetic.itertuples(index=False))
    return [
        Metric(variant, case, "rare_text_copied", share(text_found, text_total),
               f"{text_found}/{text_total}; " + "; ".join(examples[:3])),
        Metric(variant, case, "rare_number_copied", share(number_found, number_total),
               f"{number_found}/{number_total}"),
        Metric(variant, case, "row_copied", share(copies, len(synthetic)), f"{copies}/{len(synthetic)}"),
    ]


def share(part: int, total: int) -> float:
    """Divide, with NaN for an empty total.

    Args:
        part: The count.
        total: The total.

    Returns:
        The share, or NaN.
    """
    return part / total if total else float("nan")


def real_labels(real: pd.DataFrame) -> tuple[set[str], set[str]]:
    """Collect the real column names and category levels that a generic variant must hide.

    Args:
        real: The real table.

    Returns:
        The column names and the levels with a letter and 4 characters or more.
    """
    names = {name for name in real.columns if len(name) >= 4 and LETTER.search(name)}
    levels: set[str] = set()
    for name in real.columns:
        values = filled(real[name])
        if len(set(values)) <= CATEGORY_LIMIT:
            levels |= {value for value in values if len(value) >= 4 and LETTER.search(value)}
    return names, levels


def label_metrics(variant: str, case: str, real: pd.DataFrame, shared: Path) -> list[Metric]:
    """Search the files that leave the secure machine for real names and levels.

    Args:
        variant: A generic variant.
        case: The case name.
        real: The real table.
        shared: The folder of the files that leave the secure machine.

    Returns:
        The share of real column names and of real levels that the files hold.
    """
    text = "\n".join(path.read_text(encoding="utf-8", errors="replace")
                     for path in sorted(shared.iterdir()) if path.suffix != ".rds")
    names, levels = real_labels(real)
    found_names = sorted(name for name in names if word_in(name, text))
    found_levels = sorted(level for level in levels if word_in(level, text))
    return [
        Metric(variant, case, "real_name_shown", share(len(found_names), len(names)),
               f"{len(found_names)}/{len(names)}; " + "; ".join(found_names[:3])),
        Metric(variant, case, "real_level_shown", share(len(found_levels), len(levels)),
               f"{len(found_levels)}/{len(levels)}; " + "; ".join(found_levels[:3])),
    ]


def word_in(item: str, text: str) -> bool:
    """Say whether an item occurs in a text as a whole word.

    Args:
        item: A name or a level.
        text: The searched text.

    Returns:
        True when the item occurs with no letter, digit or underscore on either side.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(item)}(?![A-Za-z0-9_])", text) is not None


def audit_metrics(variant: str, case: str, real_file: Path, shared: Path) -> list[Metric]:
    """Run ``sdg audit`` on each text file that leaves the secure machine.

    Args:
        variant: The variant name.
        case: The case name.
        real_file: The real file.
        shared: The folder of the files that leave the secure machine.

    Returns:
        The count of files that fail the audit, with the count of findings.
    """
    options = ["--generic-labels"] if variant in GENERIC_VARIANTS else []
    failed = findings = 0
    kinds: Counter[str] = Counter()
    files = [path for path in sorted(shared.iterdir()) if path.suffix != ".rds"]
    for path in files:
        result = subprocess.run(["uv", "run", "sdg", "audit", str(path), str(real_file), "--json", *options],
                                capture_output=True, text=True, check=False)
        report = json.loads(result.stdout)
        failed += not report["clean"]
        findings += len(report["findings"])
        kinds.update(finding["kind"] for finding in report["findings"])
    detail = f"{failed}/{len(files)} files; " + ", ".join(f"{kind} {count}" for kind, count in kinds.most_common())
    return [Metric(variant, case, "sdg_audit_findings", float(findings), detail)]


def frequencies(values: list[str]) -> dict[str, float]:
    """Return the share of each canonical value.

    Args:
        values: Filled cells.

    Returns:
        Each value mapped to its share.
    """
    counts = Counter(canon(value) for value in values)
    return {value: count / len(values) for value, count in counts.items()}


def total_variation(real_values: list[str], synthetic_values: list[str]) -> tuple[float, bool]:
    """Compare two category distributions.

    When the synthetic column keeps the real levels, the distance pairs the
    levels by label. Otherwise it pairs the levels by rank of frequency, which
    measures the shape of the distribution only.

    Args:
        real_values: Filled real cells.
        synthetic_values: Filled synthetic cells.

    Returns:
        The total variation distance, and True when it pairs by label.
    """
    real = frequencies(real_values)
    synthetic = frequencies(synthetic_values)
    if not synthetic:
        return 1.0, False
    kept = sum(weight for level, weight in real.items() if level in synthetic)
    if kept >= 0.5:
        levels = set(real) | set(synthetic)
        return 0.5 * sum(abs(real.get(level, 0) - synthetic.get(level, 0)) for level in levels), True
    left = sorted(real.values(), reverse=True)
    right = sorted(synthetic.values(), reverse=True)
    size = max(len(left), len(right))
    left += [0.0] * (size - len(left))
    right += [0.0] * (size - len(right))
    return 0.5 * sum(abs(a - b) for a, b in zip(left, right, strict=True)), False


def numbers(column: pd.Series) -> pd.Series:
    """Convert a text column to numbers, with NaN for a missing or text cell.

    Args:
        column: A text column.

    Returns:
        The numbers.
    """
    return pd.to_numeric(column.where(~column.str.strip().isin(MISSING)), errors="coerce")


def numeric_positions(real: pd.DataFrame, synthetic: pd.DataFrame) -> list[int]:
    """Find the columns that are numeric in both tables, with 3 distinct real values or more.

    Args:
        real: The real table.
        synthetic: The synthetic table.

    Returns:
        The column positions.
    """
    positions = []
    for index in range(real.shape[1]):
        real_values = filled(real.iloc[:, index])
        if (column_kind(real_values) in {"int", "float"} and len(set(real_values)) >= 3
                and column_kind(filled(synthetic.iloc[:, index])) in {"int", "float"}):
            positions.append(index)
    return positions


def spearman(left: pd.Series, right: pd.Series) -> float:
    """Return the Spearman correlation of the complete pairs.

    Args:
        left: Numbers.
        right: Numbers.

    Returns:
        The correlation, or NaN with less than ``MIN_PAIRS`` pairs.
    """
    pairs = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(pairs) < MIN_PAIRS or pairs["left"].nunique() < 2 or pairs["right"].nunique() < 2:
        return float("nan")
    return float(stats.spearmanr(pairs["left"], pairs["right"]).statistic)


def correlation_metrics(variant: str, case: str, real: pd.DataFrame, synthetic: pd.DataFrame) -> list[Metric]:
    """Compare the correlations between the numeric columns.

    Args:
        variant: The variant name.
        case: The case name.
        real: The real table.
        synthetic: The synthetic table.

    Returns:
        The mean absolute difference of the Spearman correlations, and the
        synthetic correlation of the most correlated real pair.
    """
    keys = IDENTIFIERS.get(case, set())
    positions = [index for index in numeric_positions(real, real) if real.columns[index] not in keys]
    synthetic_positions = set(numeric_positions(real, synthetic))
    differences = []
    strongest: tuple[float, float, str] | None = None
    for left, right in combinations(positions, 2):
        real_rho = spearman(numbers(real.iloc[:, left]), numbers(real.iloc[:, right]))
        if np.isnan(real_rho):
            continue
        both = left in synthetic_positions and right in synthetic_positions
        synthetic_rho = (spearman(numbers(synthetic.iloc[:, left]), numbers(synthetic.iloc[:, right]))
                         if both else float("nan"))
        differences.append(abs(real_rho - (0.0 if np.isnan(synthetic_rho) else synthetic_rho)))
        if strongest is None or abs(real_rho) > abs(strongest[0]):
            strongest = (real_rho, synthetic_rho, f"{real.columns[left]} ~ {real.columns[right]}")
    metrics = [Metric(variant, case, "correlation_difference",
                      float(np.mean(differences)) if differences else float("nan"),
                      f"{len(differences)} pairs")]
    if strongest:
        metrics.append(Metric(variant, case, "strongest_pair_rho", strongest[1],
                              f"real {strongest[0]:.2f}; {strongest[2]}"))
    return metrics


def omega_squared(outcome: pd.Series, groups: pd.Series) -> float:
    """Return the share of the outcome variance between groups, corrected for chance.

    Args:
        outcome: Numbers.
        groups: Group labels as text.

    Returns:
        Omega squared, or NaN with less than two groups.
    """
    frame = pd.DataFrame({"y": outcome, "g": groups.str.strip()}).dropna()
    frame = frame[~frame["g"].isin(MISSING)]
    sizes = frame.groupby("g")["y"].size()
    if len(sizes) < 2 or len(frame) <= len(sizes):
        return float("nan")
    grand = frame["y"].mean()
    total = float(((frame["y"] - grand) ** 2).sum())
    means = frame.groupby("g")["y"].mean()
    between = float((sizes * (means - grand) ** 2).sum())
    within = (total - between) / (len(frame) - len(sizes))
    return (between - (len(sizes) - 1) * within) / (total + within)


def fidelity_metrics(variant: str, case: str, real: pd.DataFrame, synthetic: pd.DataFrame) -> list[Metric]:
    """Compare the structure and the distributions of a synthetic table with the real table.

    Args:
        variant: The variant name.
        case: The case name.
        real: The real table.
        synthetic: The synthetic table.

    Returns:
        The kind match, the shape match, the missing share difference, the
        Kolmogorov-Smirnov statistic, the total variation distance and the
        correlation measures.
    """
    kinds = shapes = shape_total = 0
    mismatches: list[str] = []
    missing, ks, tvd = [], [], []
    by_label = 0
    for index, name in enumerate(real.columns):
        real_values = filled(real.iloc[:, index])
        synthetic_values = filled(synthetic.iloc[:, index])
        real_kind, synthetic_kind = column_kind(real_values), column_kind(synthetic_values)
        if real_kind == synthetic_kind:
            kinds += 1
        else:
            mismatches.append(f"{name} {real_kind}->{synthetic_kind}")
        missing.append(abs((1 - len(real_values) / len(real)) - (1 - len(synthetic_values) / len(synthetic))))
        distinct = len(set(real_values))
        if real_kind in {"text", "date", "datetime"} and distinct > CATEGORY_LIMIT:
            top, count = Counter(map(shape, real_values)).most_common(1)[0]
            if count >= len(real_values) / 2:
                shape_total += 1
                shapes += bool(synthetic_values) and Counter(map(shape, synthetic_values)).most_common(1)[0][0] == top
        if (real_kind in {"int", "float"} and name not in IDENTIFIERS.get(case, set())
                and len(real_values) >= MIN_VALUES):
            # A numeric column that is no longer numeric is as far as a column can be.
            ks.append(stats.ks_2samp([float(v) for v in real_values], [float(v) for v in synthetic_values]).statistic
                      if synthetic_kind in {"int", "float"} and len(synthetic_values) >= MIN_VALUES else 1.0)
        if 2 <= distinct <= CATEGORY_LIMIT:
            distance, labelled = total_variation(real_values, synthetic_values)
            tvd.append(distance)
            by_label += labelled
    columns = real.shape[1]
    return [
        Metric(variant, case, "kind_match", kinds / columns, "; ".join(mismatches[:4])),
        Metric(variant, case, "shape_match", share(shapes, shape_total), f"{shapes}/{shape_total}"),
        Metric(variant, case, "missing_difference", float(np.mean(missing))),
        Metric(variant, case, "ks_statistic", float(np.mean(ks)) if ks else float("nan"), f"{len(ks)} columns"),
        Metric(variant, case, "category_distance", float(np.mean(tvd)) if tvd else float("nan"),
               f"{len(tvd)} columns, {by_label} by label"),
        *correlation_metrics(variant, case, real, synthetic),
    ]


def check_metrics(variant: str, case: str, real: pd.DataFrame, synthetic: pd.DataFrame) -> list[Metric]:
    """Measure the group difference and the date order that analysis code relies on.

    Args:
        variant: The variant name.
        case: The case name.
        real: The real table, for the column positions.
        synthetic: The synthetic table.

    Returns:
        The omega squared of the group check and the share of rows in date order.
    """
    metrics = []
    if case in GROUP_CHECKS:
        outcome, group = (real.columns.get_loc(name) for name in GROUP_CHECKS[case])
        value = omega_squared(numbers(synthetic.iloc[:, outcome]), synthetic.iloc[:, group])
        metrics.append(Metric(variant, case, "group_omega_squared", value, " by ".join(GROUP_CHECKS[case])))
    for first, second in ORDER_CHECKS.get(case, []):
        start = pd.to_datetime(synthetic.iloc[:, real.columns.get_loc(first)], errors="coerce", format="mixed")
        end = pd.to_datetime(synthetic.iloc[:, real.columns.get_loc(second)], errors="coerce", format="mixed")
        both = start.notna() & end.notna()
        ordered = int((end[both] >= start[both]).sum())
        metrics.append(Metric(variant, case, "date_order", share(ordered, int(both.sum())),
                              f"{first} <= {second}: {ordered}/{int(both.sum())}"))
    return metrics


def join_metric(variant: str, tables: dict[str, pd.DataFrame], reals: dict[str, pd.DataFrame]) -> list[Metric]:
    """Measure how many child keys find a parent row, as a join in analysis code does.

    Args:
        variant: The variant name.
        tables: The synthetic table of each case of the variant.
        reals: The real table of each case.

    Returns:
        The share of child rows whose key is in the parent table.
    """
    child, child_key, parent, parent_key = JOIN_CHECK
    if child not in tables or parent not in tables:
        return []
    child_column = tables[child].iloc[:, reals[child].columns.get_loc(child_key)]
    parent_column = tables[parent].iloc[:, reals[parent].columns.get_loc(parent_key)]
    keys = {canon(cell) for cell in parent_column}
    found = sum(canon(cell) in keys for cell in child_column)
    return [Metric(variant, child, "join_match", share(found, len(child_column)),
                   f"{found}/{len(child_column)} {child_key} in {parent}")]


def summary_tables(metrics: list[Metric]) -> str:
    """Write one Markdown table per metric, with the variants in rows and the cases in columns.

    Args:
        metrics: Every metric.

    Returns:
        The Markdown text.
    """
    frame = pd.DataFrame([vars(metric) for metric in metrics])
    order = ["real", *VARIANTS]
    parts = []
    for name, group in frame.groupby("metric", sort=False):
        table = group.pivot_table(index="variant", columns="case", values="value", aggfunc="first", dropna=False)
        table = table.reindex([variant for variant in order if variant in table.index])
        parts.append(f"### {name}\n\n{markdown_table(table)}\n")
    return "\n".join(parts)


def markdown_table(table: pd.DataFrame) -> str:
    """Format a table of numbers as Markdown, with 3 significant digits.

    Args:
        table: Variants in rows and cases in columns.

    Returns:
        The Markdown table.
    """
    lines = ["| variant | " + " | ".join(map(str, table.columns)) + " |",
             "|---" * (len(table.columns) + 1) + "|"]
    for variant, row in table.iterrows():
        cells = ["" if pd.isna(value) else f"{value:.3g}" for value in row]
        lines.append(f"| {variant} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    """Measure every variant of every case under a run folder.

    Returns:
        The exit code.
    """
    run_dir = Path(sys.argv[1])
    cases = read_cases(Path("scripts/comparison/cases.txt"))
    reals = {case: read_text_table(path) for case, path in cases}
    metrics: list[Metric] = []
    for case, _ in cases:
        metrics += fidelity_metrics("real", case, reals[case], reals[case])
        metrics += check_metrics("real", case, reals[case], reals[case])
    metrics += join_metric("real", reals, reals)
    for variant in VARIANTS:
        tables: dict[str, pd.DataFrame] = {}
        for case, path in cases:
            folder = run_dir / variant / case
            if not (folder / "synthetic.csv").exists():
                continue
            synthetic = read_text_table(folder / "synthetic.csv")
            if synthetic.shape[1] != reals[case].shape[1]:
                metrics.append(Metric(variant, case, "column_count", synthetic.shape[1]))
                continue
            tables[case] = synthetic
            metrics += privacy_metrics(variant, case, reals[case], synthetic)
            if variant in GENERIC_VARIANTS:
                metrics += label_metrics(variant, case, reals[case], folder / "shared")
            metrics += audit_metrics(variant, case, path, folder / "shared")
            metrics += fidelity_metrics(variant, case, reals[case], synthetic)
            metrics += check_metrics(variant, case, reals[case], synthetic)
            print(f"{variant} {case}: measured", flush=True)
        metrics += join_metric(variant, tables, reals)
    with (run_dir / "metrics.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["variant", "case", "metric", "value", "detail"])
        writer.writerows([m.variant, m.case, m.metric, m.value, m.detail] for m in metrics)
    (run_dir / "summary.md").write_text(summary_tables(metrics), encoding="utf-8")
    print(f"Wrote {run_dir / 'metrics.csv'} and {run_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
