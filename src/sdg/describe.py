"""Descriptions of the study design and the group differences of a table.

``sdg glimpse`` describes each column on its own. These descriptions show how
the rows relate, which a model-mode config needs: the subjects of each group,
the timepoints, the missing samples, the replicates, the share of variance
between subjects, and the size of the differences between groups. They run on
the secure machine, next to the real data.

Each number is a count, or an aggregate over at least ``privacy.MIN_VALUES``
values that is rounded like the glimpse. A group with fewer values prints
``n <10`` and no statistic. Names follow rule 3, so ``--generic-labels`` prints
the generic names of the glimpse, and a private table prints counts only
(rule 2).

The commands ``sdg describe design`` and ``sdg describe groups`` read a file.
The functions :func:`glimpse`, :func:`profile`, :func:`design` and :func:`groups` take a pandas
DataFrame, for data that comes from a query or a merge in a notebook.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from sdg import privacy
from sdg.config import SCHEMA_VERSION, parse_config
from sdg.formats import olink, tabular
from sdg.profile import Profile, to_yaml
from sdg.report import ColumnReport, Fact, Report, TableReport, render
from sdg.tables import (ColumnStats, Table, analyze_column, frame_table, generic_columns, is_empty, level_names,
                        number,
                        read_tables)
from sdg.version import SDG_VERSION

#: The column roles of a design, as the ``field`` of a ``kind: design`` column.
ROLES = ("subject", "group", "timepoint", "replicate", "feature", "value")

#: A column whose name matches the pattern of a role takes that role, unless
#: the role is set. ``disease_status`` is a group; ``QC_status`` is not.
ROLE_NAMES = {
    "subject": re.compile(r"subject|donor|patient|participant|individual", re.IGNORECASE),
    "group": re.compile(r"group|\barm\b|cohort|disease|diagnosis|condition|treatment|^status$", re.IGNORECASE),
    "timepoint": re.compile(r"visit|time ?point|\bweek|\bday|\bmonth|cycle|^time$", re.IGNORECASE),
    "replicate": re.compile(r"replicate|\brep\b|\bwell\b|technical", re.IGNORECASE),
    "feature": re.compile(r"analyte|assay|feature|marker|protein|gene|parameter|target|biomarker", re.IGNORECASE),
    "value": re.compile(r"value|result|concentration|\bconc\b|npx|level|measurement|intensity", re.IGNORECASE),
}

#: Column kinds that each role accepts when it is found by name.
ROLE_KINDS = {
    "subject": {"identifier", "categorical", "text", "numeric"},
    "group": {"categorical", "boolean"},
    "timepoint": {"categorical", "numeric"},
    "replicate": {"categorical", "numeric", "identifier"},
    "feature": {"categorical", "text", "identifier"},
    "value": {"numeric"},
}

#: A column found by name is a group only with this many levels or fewer.
MAX_GROUPS = 10

#: A column found by name is a timepoint only with this many values or fewer.
MAX_TIMEPOINTS = 20

#: A level that matches this pattern is the reference of the group contrasts.
REFERENCE_LEVELS = re.compile(r"control|healthy|placebo|untreated|normal|vehicle|\bctrl\b",
                              re.IGNORECASE)

#: A timepoint label that matches this pattern comes first in study order.
FIRST_TIMEPOINTS = re.compile(r"screen|baseline|\bpre\b|^bl$|^v?0$|day ?0\b|week ?0\b", re.IGNORECASE)

#: The first number in a timepoint label, such as the 12 of ``Week 12``.
LABEL_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

#: The order of the search by name. The feature and value columns come before
#: the group, because a group of a long table must not change with the feature.
SEARCH_ORDER = ("subject", "feature", "value", "timepoint", "replicate", "group")

#: Name of a DataFrame in a report, unless the caller gives one.
FRAME_NAME = "dataframe"


class NoGroupsError(ValueError):
    """A table has no group column, or fewer than two groups with values."""


@dataclass
class Layout:
    """The roles of the columns of one table, and how the report names them.

    Attributes:
        roles: Each found role mapped to its column position.
        features: Positions of the numeric columns that are features of a wide table.
        names: Printed name of each column, real or generic.
        levels: For each column with generic levels, each real level mapped to its generic name.
        kinds: Kind of each column.
        candidates: Positions of the columns that could be the group, when none was found.
    """

    roles: dict[str, int]
    features: list[int]
    names: list[str]
    levels: dict[int, dict[str, str]]
    kinds: list[str]
    candidates: list[int] = field(default_factory=list)

    def level(self, position: int, value: str) -> str:
        """Return the printed text of one value of a role column.

        Args:
            position: Column position.
            value: The cell text.

        Returns:
            The generic level, or the value itself.
        """
        return self.levels.get(position, {}).get(value, value)

    def role_name(self, role: str) -> str:
        """Return the printed name of the column of a role.

        Args:
            role: The role.

        Returns:
            The quoted column name, or ``none``.
        """
        return repr(self.names[self.roles[role]]) if role in self.roles else "none"

    @property
    def long(self) -> bool:
        """Say whether the table has one row per sample and feature."""
        return "feature" in self.roles and "value" in self.roles


@dataclass
class Sample:
    """The values of one sample and one feature.

    Attributes:
        subject: Subject key.
        group: Group value, or None.
        timepoint: Timepoint value, or None.
        values: The value of each replicate row.
    """

    subject: str
    group: str | None
    timepoint: str | None
    values: list[float] = field(default_factory=list)

    @property
    def mean(self) -> float:
        """Return the mean of the replicates."""
        return float(np.mean(self.values))


def cell_text(value: object) -> str | None:
    """Return the text of a role cell, or None for an empty cell.

    Args:
        value: A cell as read.

    Returns:
        The text, as the glimpse counts it.
    """
    return None if is_empty(value) else str(value)


def constant_within(table: Table, position: int, keys: list[int]) -> bool:
    """Say whether a column holds one value for each combination of key columns.

    Args:
        table: The table.
        position: The column.
        keys: Positions of the key columns. With none, the answer is True.

    Returns:
        True when no key combination has two values.
    """
    if not keys:
        return True
    seen: dict[tuple, str | None] = {}
    for row in table.rows:
        value = cell_text(row[position])
        if seen.setdefault(tuple(cell_text(row[key]) for key in keys), value) != value:
            return False
    return True


def find_role(role: str, table: Table, columns: list[ColumnStats], roles: dict[str, int]) -> int | None:
    """Find the column of one role from the column names.

    A subject column can also be the first identifier column whose values
    repeat, as an ``ID`` column of a longitudinal table does. A timepoint
    must change within a subject, so ``DAYS_TO_DIAGNOSIS`` of a table with one
    row per patient is no timepoint. A group of a long table must hold one
    value per sample, so ``OlinkID`` is no group.

    Args:
        role: The role.
        table: The table.
        columns: The statistics of each column.
        roles: The roles found so far.

    Returns:
        The column position, or None.
    """
    taken = set(roles.values())
    for position, stats in enumerate(columns):
        if position in taken or stats.kind not in ROLE_KINDS[role] or not stats.filled:
            continue
        if not ROLE_NAMES[role].search(str(table.header[position])):
            continue
        if role == "group" and not (2 <= stats.unique <= MAX_GROUPS
                                    and constant_within(table, position, sample_keys(roles))):
            continue
        if role == "timepoint" and (stats.unique > MAX_TIMEPOINTS or "subject" not in roles
                                    or constant_within(table, position, [roles["subject"]])):
            continue
        return position
    if role == "subject":
        return next((position for position, stats in enumerate(columns)
                     if position not in taken and stats.kind == "identifier" and stats.unique < stats.filled), None)
    return None


def column_position(table: Table, name: str, role: str) -> int:
    """Return the position of a column that the caller named.

    Args:
        table: The table.
        name: The real column name.
        role: The role, for the message.

    Returns:
        The position of the first column with that name.

    Raises:
        ValueError: If no column has that name.
    """
    header = [str(item) for item in table.header]
    if name not in header:
        raise ValueError(f"{role}: no column named {name!r}")
    return header.index(name)


def group_candidates(table: Table, columns: list[ColumnStats], taken: set[int], keys: list[int]) -> list[int]:
    """List the columns that could be the group: few levels, no role yet.

    Args:
        table: The table.
        columns: The statistics of each column.
        taken: Positions of the columns that already have a role.
        keys: Positions of the columns of one sample in a long table.

    Returns:
        The positions.
    """
    return [position for position, stats in enumerate(columns)
            if position not in taken and stats.kind in ROLE_KINDS["group"] and 2 <= stats.unique <= MAX_GROUPS
            and constant_within(table, position, keys)]


def table_layout(table: Table, given: dict[str, str | None], private: bool,
                 options: privacy.Options) -> Layout:
    """Find the role of each column and the printed names.

    Args:
        table: The table.
        given: Each role mapped to the real column name that the caller set,
            ``""`` for no column, or None to find it by name.
        private: The table records counts only, so its levels are generic.
        options: The description options.

    Returns:
        The layout.

    Raises:
        ValueError: If a named column does not exist.
    """
    columns = [analyze_column(name, table.column(index), options.max_categories)
               for index, name in enumerate(table.header)]
    roles: dict[str, int] = {}
    for role in ROLES:
        name = given.get(role)
        if name:
            roles[role] = column_position(table, name, role)
    for role in SEARCH_ORDER:
        if role == "group" and ("feature" in roles) != ("value" in roles):
            roles.pop("feature", None)
            roles.pop("value", None)
        if role not in roles and given.get(role) is None:
            position = find_role(role, table, columns, roles)
            if position is not None:
                roles[role] = position
    generic = generic_columns(columns, options.generic_labels)
    layout = Layout(roles=roles,
                    features=[] if "value" in roles else
                    [position for position, stats in enumerate(columns)
                     if stats.kind == "numeric" and position not in roles.values()],
                    names=[generic.get(stats.name, str(stats.name)) for stats in columns],
                    levels={position: level_names(stats) for position, stats in enumerate(columns)
                            if stats.kind == "categorical" and (options.generic_labels or private)},
                    kinds=[stats.kind for stats in columns])
    if "group" not in roles and given.get("group") is None:
        layout.candidates = group_candidates(table, columns, set(roles.values()), sample_keys(roles))
    return layout


def sample_keys(roles: dict[str, int]) -> list[int]:
    """Return the columns that name one sample of a long table.

    Args:
        roles: The roles found so far.

    Returns:
        The subject and timepoint positions in a long table, else nothing.
    """
    if "feature" not in roles or "value" not in roles:
        return []
    return [roles[role] for role in ("subject", "timepoint") if role in roles]


def feature_labels(table: Table, layout: Layout) -> dict[object, str]:
    """Name the features as the report prints them.

    A feature of a wide table is a column. A feature of a long table is a
    level of the feature column: it keeps its text when the glimpse prints the
    levels of that column, and is ``feature_<n>`` when the glimpse prints
    shapes only, as for an identifier or free text column (rules 1 and 6).

    Args:
        table: The table.
        layout: The layout.

    Returns:
        Each feature key, a column position or a feature text, mapped to its
        printed name.
    """
    if not layout.long:
        return {position: layout.names[position] for position in layout.features}
    position = layout.roles["feature"]
    found = list(dict.fromkeys(text for text in map(cell_text, table.column(position)) if text is not None))
    if layout.kinds[position] == "categorical":
        return {text: layout.level(position, text) for text in found}
    return {text: f"feature_{index}" for index, text in enumerate(found, start=1)}


def collect_samples(table: Table, layout: Layout) -> tuple[dict[tuple, Sample], int]:
    """Gather the numeric values of each sample and feature.

    A sample is a subject in a group at a timepoint. Without a subject column,
    each row is its own subject. Rows of the same sample and feature are
    replicates.

    Args:
        table: The table.
        layout: The layout.

    Returns:
        The samples by subject, group, timepoint and feature key, and the
        count of rows skipped for an empty subject cell.
    """
    roles = layout.roles
    samples: dict[tuple, Sample] = {}
    skipped = 0
    for index, row in enumerate(table.rows):
        if all(is_empty(cell) for cell in row):
            continue
        subject = cell_text(row[roles["subject"]]) if "subject" in roles else f"row {index}"
        if subject is None:
            skipped += 1
            continue
        group = cell_text(row[roles["group"]]) if "group" in roles else None
        timepoint = cell_text(row[roles["timepoint"]]) if "timepoint" in roles else None
        if layout.long:
            pairs = [(cell_text(row[roles["feature"]]), number(row[roles["value"]]))]
        else:
            pairs = [(position, number(row[position])) for position in layout.features]
        for feature, value in pairs:
            key = (subject, group, timepoint, feature)
            sample = samples.setdefault(key, Sample(subject, group, timepoint))
            if feature is not None and value is not None:
                sample.values.append(value)
    return samples, skipped


def timepoint_key(label: str, seen: int, numeric: bool) -> tuple:
    """Sort key of a timepoint label in study order.

    Args:
        label: The timepoint label.
        seen: Position of its first row, which breaks a tie.
        numeric: The timepoint column is numeric.

    Returns:
        A key: a first timepoint such as ``baseline``, then the number in the
        label, then the order of the rows.
    """
    found = number(label) if numeric else None
    if found is None and (match := LABEL_NUMBER.search(label)):
        found = float(match.group())
    first = 0 if FIRST_TIMEPOINTS.search(label) else 1
    return (first if not numeric else 1, math.inf if found is None else found, seen)


def ordered_timepoints(table: Table, layout: Layout) -> list[str]:
    """List the timepoint labels in study order.

    Args:
        table: The table.
        layout: The layout, with a timepoint column.

    Returns:
        The labels.
    """
    position = layout.roles["timepoint"]
    labels = list(dict.fromkeys(text for text in map(cell_text, table.column(position)) if text is not None))
    numeric = layout.kinds[position] == "numeric"
    return [label for _, label in sorted((timepoint_key(label, index, numeric), label)
                                         for index, label in enumerate(labels))]


def normal_scores(values: np.ndarray) -> np.ndarray:
    """Turn values into normal scores, the scale of the model mode.

    Args:
        values: The values.

    Returns:
        The inverse normal of each rank, with ties averaged.
    """
    return scipy_stats.norm.ppf((scipy_stats.rankdata(values) - 0.5) / len(values))


def subject_icc(pairs: list[tuple[str, float]]) -> float | None:
    """Estimate the share of variance between subjects of one feature.

    The model mode draws a subject intercept and a sample residual on the
    normal scale, so the estimate works on normal scores: a one-way random
    effects ICC over the subjects with two samples or more.

    Args:
        pairs: The subject and the value of each sample.

    Returns:
        The ICC between 0 and 1, or None with fewer than ``MIN_VALUES``
        subjects of two samples.
    """
    if len(pairs) < 2:
        return None
    scores = normal_scores(np.array([value for _, value in pairs]))
    by_subject: dict[str, list[float]] = defaultdict(list)
    for (subject, _), score in zip(pairs, scores, strict=True):
        by_subject[subject].append(float(score))
    groups = [np.array(values) for values in by_subject.values() if len(values) >= 2]
    count, total = len(groups), sum(len(values) for values in groups)
    if count < privacy.MIN_VALUES or total <= count:
        return None
    grand = np.concatenate(groups).mean()
    between = sum(len(values) * (values.mean() - grand) ** 2 for values in groups) / (count - 1)
    within = sum(((values - values.mean()) ** 2).sum() for values in groups) / (total - count)
    size = (total - sum(len(values) ** 2 for values in groups) / total) / (count - 1)
    denominator = between + (size - 1) * within
    if denominator <= 0:
        return None
    return float(min(1.0, max(0.0, (between - within) / denominator)))


def replicate_cv(samples: list[Sample]) -> float | None:
    """Return the median coefficient of variation between replicates, in percent.

    Args:
        samples: The samples of one feature.

    Returns:
        The median over the samples with replicates and a positive mean, or
        None with fewer than ``MIN_VALUES`` such samples.
    """
    cvs = [float(np.std(sample.values, ddof=1)) / sample.mean * 100 for sample in samples
           if len(sample.values) >= 2 and sample.mean > 0]
    return float(np.median(cvs)) if len(cvs) >= privacy.MIN_VALUES else None


def spread_text(values: list[float]) -> str:
    """Print a median with its quartiles and the IQR range of several estimates.

    Args:
        values: The estimates.

    Returns:
        Text such as ``0.62 (median of 12 features, quartiles 0.51 to 0.7)``.
    """
    low, middle, high = np.percentile(values, [25, 50, 75])
    return (f"{privacy.format_number(middle)} (median of {len(values)} feature(s), quartiles "
            f"{privacy.format_number(low)} to {privacy.format_number(high)})")


def role_facts(layout: Layout) -> list[Fact]:
    """Print the column of each role, and the group candidates.

    Args:
        layout: The layout.

    Returns:
        The facts.
    """
    facts = [Fact(f"{role} column", layout.role_name(role)) for role in ROLES
             if role in layout.roles or role in ("subject", "group", "timepoint")]
    if layout.candidates:
        names = dict.fromkeys(layout.names[position] for position in layout.candidates)
        facts.append(Fact("group candidates", ", ".join(repr(name) for name in names)))
    return facts


def design_report(table: Table, layout: Layout, label: str, private: bool) -> TableReport:
    """Describe the study design of one table.

    A sample is a subject in a group at a timepoint. When a subject is in
    several groups, as a subject whose samples got three treatments, each
    treatment is a sample of its own.

    Args:
        table: The table.
        layout: The layout.
        label: Table label.
        private: The table records counts only, so no ICC or CV prints.

    Returns:
        The report: table facts, then one line per group and per timepoint.
    """
    roles = layout.roles
    content = [(index, row) for index, row in enumerate(table.rows) if not all(is_empty(cell) for cell in row)]
    keys: set[tuple] = set()
    for index, row in content:
        subject = cell_text(row[roles["subject"]]) if "subject" in roles else f"row {index}"
        if subject is not None:
            keys.add((subject, cell_text(row[roles["group"]]) if "group" in roles else None,
                      cell_text(row[roles["timepoint"]]) if "timepoint" in roles else None))
    subjects = {key[0] for key in keys}
    facts = [Fact("rows", str(len(content))), *role_facts(layout), Fact("subjects", str(len(subjects))),
             Fact("samples", str(len(keys)))]
    if keys:
        counts = sorted(Counter(key[0] for key in keys).values())
        facts.append(Fact("samples per subject", f"{counts[0]} to {counts[-1]}, median "
                                                 f"{privacy.format_number(float(np.median(counts)))}"))
    groups_of: dict[str, set[str]] = defaultdict(set)
    for subject, group, _ in keys:
        if group is not None:
            groups_of[subject].add(group)
    several = sum(len(values) > 1 for values in groups_of.values())
    if several:
        facts.append(Fact("subjects in several groups", str(several)))
    timepoints = ordered_timepoints(table, layout) if "timepoint" in roles else []
    if len(timepoints) > 1 and "subject" in roles:
        later = {(subject, timepoint) for subject, _, timepoint in keys
                 if timepoint is not None and timepoint != timepoints[0]}
        facts.append(Fact("missing_samples", privacy.format_number(
            1 - len(later) / (len(subjects) * (len(timepoints) - 1)))))
    samples, skipped = collect_samples(table, layout)
    if skipped:
        facts.append(Fact("rows without a subject", str(skipped)))
    replicates = Counter(len(sample.values) for sample in samples.values() if sample.values)
    common = replicates.most_common(1)[0][0] if replicates else 1
    replicated = common > 1 and "replicate" in roles
    if replicated:
        facts.append(Fact("replicates.n", str(common)))
    elif common > 1:
        # With no replicate column, the rows of one sample can be replicates or
        # a factor that has no column role, such as the lab items of MIMIC.
        facts.append(Fact("rows per sample", str(common)))
    else:
        facts.append(Fact("replicates", "none"))
    if layout.long:
        facts.append(Fact("features", str(len({key[3] for key in samples if key[3] is not None}))))
    else:
        facts.append(Fact("features", str(len(layout.features))))
    if private:
        facts.append(Fact("private", "counts only"))
    else:
        facts += statistic_facts(samples, replicated)
    report = TableReport(label, "design", facts)
    by_group: dict[str, set[tuple]] = defaultdict(set)
    for key in keys:
        if key[1] is not None:
            by_group[key[1]].add(key)
    sizes = Counter({group: len({key[0] for key in members}) for group, members in by_group.items()})
    for group, count in sizes.most_common():
        line = [Fact("subjects", str(count)), Fact("samples", str(len(by_group[group])))]
        line += [Fact(repr(layout.level(roles["timepoint"], timepoint)),
                      str(sum(key[2] == timepoint for key in by_group[group]))) for timepoint in timepoints]
        report.columns.append(ColumnReport(layout.level(roles["group"], group), "group", line))
    for order, timepoint in enumerate(timepoints, start=1):
        line = [Fact("order", str(order)), Fact("subjects", str(len({key[0] for key in keys if key[2] == timepoint})))]
        report.columns.append(ColumnReport(layout.level(roles["timepoint"], timepoint), "timepoint", line))
    return report


def statistic_facts(samples: dict[tuple, Sample], replicated: bool) -> list[Fact]:
    """Estimate the ICC and the replicate CV over the features.

    Args:
        samples: The samples by subject, group, timepoint and feature key.
        replicated: The samples have replicates.

    Returns:
        The ``icc`` fact and the ``replicates.cv_pct`` fact, when estimable.
    """
    by_feature: dict[object, list[Sample]] = defaultdict(list)
    for (_, _, _, feature), sample in samples.items():
        if feature is not None and sample.values:
            by_feature[feature].append(sample)
    iccs = [value for items in by_feature.values()
            if (value := subject_icc([(sample.subject, sample.mean) for sample in items])) is not None]
    facts = [Fact("icc", spread_text(iccs))] if iccs else []
    if replicated:
        cvs = [value for items in by_feature.values() if (value := replicate_cv(items)) is not None]
        if cvs:
            facts.append(Fact("replicates.cv_pct", spread_text(cvs)))
    return facts


def reference_level(levels: Counter, given: str | None) -> str:
    """Choose the reference group of the contrasts.

    Args:
        levels: Each group and its sample count.
        given: The reference that the caller set, or None.

    Returns:
        The given level, else the first level that names a control, else the
        largest group.

    Raises:
        ValueError: If the given reference is not a group.
    """
    if given is not None:
        if given not in levels:
            raise ValueError(f"reference: no group named {given!r}")
        return given
    return next((level for level in levels if REFERENCE_LEVELS.search(level)), levels.most_common(1)[0][0])


def group_text(values: list[float]) -> str:
    """Print the size, the median and the quartiles of one group.

    Args:
        values: The sample values of the group.

    Returns:
        ``n <10`` for a small group, else text such as ``n 30, median 28.9,
        quartiles 22.8 to 34.9``.
    """
    if len(values) < privacy.MIN_VALUES:
        return f"n <{privacy.MIN_VALUES}"
    low, middle, high = np.percentile(values, [25, 50, 75])
    return (f"n {len(values)}, median {privacy.format_number(middle)}, quartiles "
            f"{privacy.format_number(low)} to {privacy.format_number(high)}")


def contrast_text(values: list[float], reference: list[float]) -> str | None:
    """Print the ratio and the difference of the medians of two groups.

    Args:
        values: The sample values of the moved group.
        reference: The sample values of the reference group.

    Returns:
        Text such as ``ratio 1.33, log2 ratio 0.41, difference 9.5``, or None
        when a group is small.
    """
    if len(values) < privacy.MIN_VALUES or len(reference) < privacy.MIN_VALUES:
        return None
    moved, base = float(np.median(values)), float(np.median(reference))
    parts = []
    if moved > 0 and base > 0:
        parts += [f"ratio {privacy.format_number(moved / base)}",
                  f"log2 ratio {privacy.format_number(math.log2(moved / base))}"]
    parts.append(f"difference {privacy.format_number(moved - base)}")
    return ", ".join(parts)


def groups_report(table: Table, layout: Layout, label: str, private: bool, reference: str | None) -> TableReport:
    """Describe the differences between the groups of one table.

    Each sample counts once: its replicates are averaged first.

    Args:
        table: The table.
        layout: The layout, with a group column.
        label: Table label.
        private: The table records counts only, so no statistic prints.
        reference: The real reference level, or None to choose it.

    Returns:
        The report: table facts, then one line per feature and timepoint.

    Raises:
        NoGroupsError: If the table has no group column or fewer than two groups.
    """
    roles = layout.roles
    if "group" not in roles:
        candidates = ", ".join(repr(str(table.header[position])) for position in layout.candidates)
        raise NoGroupsError("no group column found; set one" + (f", such as {candidates}" if candidates else ""))
    samples, skipped = collect_samples(table, layout)
    filled = [sample for sample in samples.values() if sample.values and sample.group is not None]
    sizes = Counter(sample.group for sample in {(s.subject, s.group, s.timepoint): s for s in filled}.values())
    if len(sizes) < 2:
        raise NoGroupsError(f"group column {str(table.header[roles['group']])!r} has fewer than two groups "
                            "with values")
    base = reference_level(sizes, reference)
    order = [base] + [level for level, _ in sizes.most_common() if level != base]
    facts = [Fact("rows", str(sum(not all(is_empty(cell) for cell in row) for row in table.rows))),
             *role_facts(layout), Fact("reference", repr(layout.level(roles["group"], base))),
             Fact("samples", ", ".join(f"{layout.level(roles['group'], level)!r} {sizes[level]}" for level in order))]
    if skipped:
        facts.append(Fact("rows without a subject", str(skipped)))
    if any(len(sample.values) > 1 for sample in filled):
        facts.append(Fact("repeated rows", "averaged per sample"))
    if private:
        facts.append(Fact("private", "counts only"))
    report = TableReport(label, "groups", facts)
    if private:
        return report
    timepoints = ordered_timepoints(table, layout) if "timepoint" in roles else [None]
    names = feature_labels(table, layout)
    kind = "feature" if layout.long else "numeric"
    values: dict[tuple, list[float]] = defaultdict(list)
    for (_, _, timepoint, feature), sample in samples.items():
        if sample.values and sample.group is not None:
            values[(feature, timepoint, sample.group)].append(sample.mean)
    for feature, name in names.items():
        for timepoint in timepoints:
            line = [Fact("timepoint", repr(layout.level(roles["timepoint"], timepoint)))] if timepoint else []
            line += [Fact(repr(layout.level(roles["group"], level)), group_text(values[(feature, timepoint, level)]))
                     for level in order]
            for level in order[1:]:
                text = contrast_text(values[(feature, timepoint, level)], values[(feature, timepoint, base)])
                if text:
                    line.append(Fact(f"{layout.level(roles['group'], level)!r} vs "
                                     f"{layout.level(roles['group'], base)!r}", text))
            report.columns.append(ColumnReport(name, kind, line))
    return report


def describe_table(command: str, path: Path, label: str, table: Table, given: dict[str, str | None],
                   options: privacy.Options, reference: str | None = None) -> TableReport:
    """Describe one table as a design or as group differences.

    Args:
        command: ``design`` or ``groups``.
        path: The file, read for its name only.
        label: Table label.
        table: The table.
        given: The role columns that the caller set; see :func:`table_layout`.
        options: The description options.
        reference: The real reference group of ``groups``, or None.

    Returns:
        The table report.
    """
    private = tabular.is_private_table(path, table, options)
    layout = table_layout(table, given, private, options)
    if command == "design":
        return design_report(table, layout, label, private)
    return groups_report(table, layout, label, private, reference)


def sheet_labels(tables: list[Table], generic_labels: bool) -> dict[str, str]:
    """Name each sheet of a workbook as the glimpse prints it.

    Args:
        tables: Every table of the file, with or without a header.
        generic_labels: Replace the sheet names with ``sheet_<n>``.

    Returns:
        Each real sheet name mapped to its printed name; empty for a text file.
    """
    sheets = [table.sheet for table in tables if table.sheet is not None]
    return privacy.generic_names(sheets, "sheet") if generic_labels else {sheet: sheet for sheet in sheets}


def describe_file(command: str, path: Path, given: dict[str, str | None], options: privacy.Options,
                  sheet: str | None = None, reference: str | None = None) -> Report:
    """Describe one CSV, TSV or Excel file.

    Args:
        command: ``design`` or ``groups``.
        path: The file.
        given: The role columns that the caller set; see :func:`table_layout`.
        options: The description options.
        sheet: The sheet of a workbook, or None for the first sheet with a header.
        reference: The real reference group of ``groups``, or None.

    Returns:
        The report of one table.

    Raises:
        ValueError: If the file has no table with a header, or no such sheet,
            or if it is a wide Olink file, which is not a plain table.
    """
    if olink.is_wide(path):
        raise ValueError(f"{path.name}: a wide Olink file is not a plain table; describe the long export, "
                         "or a DataFrame of samples by assays")
    every = [tabular.kept_table(table, options) for table in read_tables(path)]
    tables = [table for table in every if table.header]
    if sheet is not None:
        tables = [table for table in tables if table.sheet == sheet]
    if not tables:
        raise ValueError(f"{path.name}: no table with a header" + (f" in sheet {sheet!r}" if sheet else ""))
    table = tables[0]
    file_label = privacy.generic_file_label(1, path)
    shown = sheet_labels(every, options.generic_labels).get(table.sheet) if table.sheet else None
    report = describe_table(command, path, tabular.table_label(file_label, shown), table, given, options, reference)
    return Report(tables=[report], comments=[f"sdg describe {command} {SDG_VERSION}"])


def frame_options(generic_labels: bool, private: str, max_categories: int,
                  key: privacy.LabelKey | None = None) -> privacy.Options:
    """Build the options of a DataFrame description.

    Args:
        generic_labels: Replace the names with generic names.
        private: A name that matches this pattern records counts only.
        max_categories: Most categories listed per column.
        key: A label key to fill, or None.

    Returns:
        The options.
    """
    return privacy.Options(private_files=re.compile(private, re.IGNORECASE), generic_labels=generic_labels,
                           max_categories=max_categories, key=key)


def glimpse(frame: pd.DataFrame, name: str = FRAME_NAME, generic_labels: bool = False,
            private: str = privacy.DEFAULT_PRIVATE_FILES, max_categories: int = privacy.MAX_LEVELS,
            label_key: str | Path | None = None) -> str:
    """Print the glimpse report of a DataFrame, as ``sdg glimpse`` does for a file.

    Args:
        frame: The real data.
        name: Name of the data, such as the file name that the synthetic
            data will have. Its suffix sets the format, so ``clinical.xlsx``
            reports an Excel table that compares with the glimpse of a
            synthetic workbook. A name that matches ``private`` makes the table
            private. The report prints the name as ``file_1`` and its suffix.
        generic_labels: Replace the column names and the levels with generic names.
        private: A name that matches this pattern records counts only.
        max_categories: Most categories, shapes or tokens listed per column.
        label_key: Write a CSV of each generic name and the real name behind
            it to this file, for :func:`sdg.relabel.relabel_frame`. The key
            holds real names: keep it on the secure machine.

    Returns:
        The report text.
    """
    options = frame_options(generic_labels, private, max_categories,
                            privacy.LabelKey() if label_key is not None else None)
    path = Path(name)
    label = privacy.generic_file_label(1, path)
    privacy.note_key(options, "file", "", None, "", {name: label})
    table = tabular.glimpse_table(path, label, frame_table(frame), None, options)
    if options.key is not None:
        options.key.write(Path(label_key))
    return render(Report(tables=[table], comments=[f"sdg glimpse {SDG_VERSION}", "files: 1"]))


def profile(frame: pd.DataFrame, name: str = f"{FRAME_NAME}.csv", seed: int = 0, generic_labels: bool = False,
            private: str = privacy.DEFAULT_PRIVATE_FILES, max_categories: int = privacy.MAX_LEVELS,
            label_key: str | Path | None = None) -> str:
    """Write the config of a DataFrame, as ``sdg profile`` does for a file.

    The config holds the same facts as the profile of a file under the same
    privacy rules, and no real value. Read it before it leaves the secure
    machine.

    Args:
        frame: The real data.
        name: Name of the data. Its suffix sets the format of the synthetic
            file: ``.csv``, ``.tsv``, ``.xlsx`` or ``.parquet``. A name that
            matches ``private`` makes the table private.
        seed: Seed to write into the config.
        generic_labels: Replace the column names and the levels with generic names.
        private: A name that matches this pattern makes the table private.
        max_categories: Most categories listed per column.
        label_key: Write a CSV of each generic name and the real name behind it
            to this file. Keep it on the secure machine.

    Returns:
        The YAML config, with a header that lists the default fields.
    """
    options = frame_options(generic_labels, private, max_categories,
                            privacy.LabelKey() if label_key is not None else None)
    path = Path(name)
    label = privacy.generic_file_label(1, path)
    privacy.note_key(options, "file", "", None, "", {name: label})
    table = frame_table(frame)
    typed = privacy.file_suffix(path) in tabular.TYPED_SUFFIXES
    sheet, defaults = tabular.sheet_config(path, label, table, "sheet_1", options, typed)
    dataset = {"name": label.split(".", 1)[0], "format": tabular.format_of(path, table), "file": label,
               "sheets": [sheet]}
    result = Profile({"version": SCHEMA_VERSION, "seed": seed, "mode": "resample", "datasets": [dataset]},
                     [f"datasets[0].sheets[0].{item}" for item in defaults])
    parse_config(result.config)
    if options.key is not None:
        options.key.write(Path(label_key))
    return to_yaml(result)


def design(frame: pd.DataFrame, subject: str | None = None, group: str | None = None,
           timepoint: str | None = None, replicate: str | None = None, feature: str | None = None,
           value: str | None = None, name: str = FRAME_NAME, generic_labels: bool = False,
           private: str = privacy.DEFAULT_PRIVATE_FILES, max_categories: int = privacy.MAX_LEVELS) -> str:
    """Describe the study design of a DataFrame, as ``sdg describe design`` does for a file.

    Each role argument names a real column. None finds the column by its
    name, and ``""`` sets no column for the role.

    Args:
        frame: The real data.
        subject: The subject column.
        group: The group column.
        timepoint: The timepoint column.
        replicate: The replicate column, such as a well.
        feature: The feature column of a long table, such as an assay.
        value: The value column of a long table, such as NPX.
        name: Name of the data; see :func:`glimpse`.
        generic_labels: Replace the column names and the levels with generic names.
        private: A name that matches this pattern records counts only.
        max_categories: Most categories listed per column.

    Returns:
        The report text.
    """
    given = {"subject": subject, "group": group, "timepoint": timepoint, "replicate": replicate,
             "feature": feature, "value": value}
    options = frame_options(generic_labels, private, max_categories)
    path = Path(name)
    table = describe_table("design", path, privacy.generic_file_label(1, path), frame_table(frame), given, options)
    return render(Report(tables=[table], comments=[f"sdg describe design {SDG_VERSION}"]))


def groups(frame: pd.DataFrame, group: str | None = None, reference: str | None = None,
           subject: str | None = None, timepoint: str | None = None, replicate: str | None = None,
           feature: str | None = None, value: str | None = None, name: str = FRAME_NAME,
           generic_labels: bool = False, private: str = privacy.DEFAULT_PRIVATE_FILES,
           max_categories: int = privacy.MAX_LEVELS) -> str:
    """Describe the group differences of a DataFrame, as ``sdg describe groups`` does for a file.

    Each role argument names a real column. None finds the column by its
    name, and ``""`` sets no column for the role.

    Args:
        frame: The real data. Merge the group column in first when it lives
            in another table, and drop private columns such as age and sex,
            or the report prints counts only.
        group: The group column.
        reference: The real name of the reference group. None chooses a
            control level, else the largest group.
        subject: The subject column.
        timepoint: The timepoint column. With one, each timepoint has its own line.
        replicate: The replicate column, such as a well.
        feature: The feature column of a long table, such as an assay.
        value: The value column of a long table, such as NPX.
        name: Name of the data; see :func:`glimpse`.
        generic_labels: Replace the column names and the levels with generic names.
        private: A name that matches this pattern records counts only.
        max_categories: Most categories listed per column.

    Returns:
        The report text.
    """
    given = {"subject": subject, "group": group, "timepoint": timepoint, "replicate": replicate,
             "feature": feature, "value": value}
    options = frame_options(generic_labels, private, max_categories)
    path = Path(name)
    table = describe_table("groups", path, privacy.generic_file_label(1, path), frame_table(frame), given, options,
                           reference)
    return render(Report(tables=[table], comments=[f"sdg describe groups {SDG_VERSION}"]))
