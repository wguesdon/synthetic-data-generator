"""``sdg compare``: list the differences between two glimpse reports.

The first report comes from the real data, the second from the synthetic
output. A difference in structure, such as a column name, a kind, a level set or
a delimiter, is an error: analysis code that works on the synthetic data can
fail on the real data. A difference in values, such as a range or a count, is
expected and is not reported; a difference that can come from chance, such as a
rare level that the synthetic data did not draw, is a note.

A fixture with invented column names can hide the delivered names, so the code
works on the synthetic data and fails on the real data. A compare of the two
reports finds that error before the real run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sdg.report import ColumnReport, Report, TableReport, fact_map, glimpse_tables, parse_counted

#: Table facts whose difference is an error.
TABLE_ERRORS = {"rows", "columns", "delimiter", "repeated names", "paired", "header style", "channels",
                "code classes", "samples", "assays", "layers", "obsm", "orientation", "quality offset",
                "value column", "panels", "header row", "comment marks", "footer rows"}

#: Table facts whose difference is a note. Other table facts are values.
TABLE_NOTES = {"empty rows", "ragged rows", "private", "compressed", "read length", "datatype"}

#: Column facts that list items, whose item sets must agree.
LISTED_ERRORS = {"values", "tokens"}

#: Column facts that list items, whose item sets can differ by chance.
LISTED_NOTES = {"shapes"}

#: Column facts whose difference is an error, such as the marker label of an
#: FCS channel, which gating code reads.
COLUMN_ERRORS = {"label", "suffix"}

#: Column facts whose difference is a note. Other column facts are values.
COLUMN_NOTES = {"decimals", "format"}

#: A missing level whose real share is below this is a note, not an error.
RARE_SHARE = 0.05

#: Cell type names that count as the same type.
CELL_TYPE_GROUPS = {"int": "number", "float": "number", "datetime": "datetime", "date": "datetime"}


@dataclass(frozen=True)
class Difference:
    """One difference between the real and the synthetic report.

    Attributes:
        level: ``error`` for a difference in structure, ``note`` for one that
            can come from chance.
        where: The table, and the column when there is one.
        message: What differs.
    """

    level: str
    where: str
    message: str


def sheet_of(label: str) -> str:
    """Return the sheet part of a table label.

    Args:
        label: A label such as ``file_1.xlsx | sheet 'Results'``.

    Returns:
        The sheet part, or an empty text for a text file.
    """
    return label.partition(" | ")[2]


def cell_type_counts(value: str) -> Counter:
    """Read a ``cell types`` fact as grouped type names with their counts.

    Args:
        value: The fact value, such as ``float 93, str 27``.

    Returns:
        The count of each type name, with int and float grouped as ``number``.
    """
    counts: Counter = Counter()
    for name, count in parse_counted(value)[0].items():
        counts[CELL_TYPE_GROUPS.get(name, name)] += count
    return counts


def compare_cell_types(real: str, synthetic: str, filled: int, where: str) -> list[Difference]:
    """Compare two ``cell types`` facts.

    Args:
        real: The real fact value.
        synthetic: The synthetic fact value.
        filled: Filled cells of the real column.
        where: Location for the message.

    Returns:
        No difference when the type sets agree. Else an error, or a note when
        the synthetic data only lacks rare real types, such as the one text
        cell of a numeric column of 21 rows.
    """
    real_types, synthetic_types = cell_type_counts(real), cell_type_counts(synthetic)
    if set(real_types) == set(synthetic_types):
        return []
    rare = all(real_types[name] / max(filled, 1) < RARE_SHARE for name in set(real_types) - set(synthetic_types))
    level = "note" if rare and set(synthetic_types) <= set(real_types) else "error"
    return [Difference(level, where, f"cell types: real {sorted(real_types)}, synthetic {sorted(synthetic_types)}")]


def compare_listed(key: str, real: str, synthetic: str, where: str, filled: int = 0) -> list[Difference]:
    """Compare a fact that lists items, such as the levels of a column.

    Args:
        key: The fact key.
        real: The real fact value.
        synthetic: The synthetic fact value.
        where: Location for the message.
        filled: Filled cells of the real column. A share is taken over them,
            or over the listed items when this is 0. The tokens of a numeric
            column need it, because they list only the non-numeric cells.

    Returns:
        An error for an item that only the synthetic data has, or for a common
        real item that it lacks; a note for a rare real item that it lacks.
    """
    real_items, real_more = parse_counted(real)
    synthetic_items, synthetic_more = parse_counted(synthetic)
    if real_more or synthetic_more:
        return []
    differences = []
    extra = sorted(set(synthetic_items) - set(real_items))
    if extra:
        level = "note" if key in LISTED_NOTES else "error"
        differences.append(Difference(level, where, f"{key}: only the synthetic data has {extra}"))
    total = filled or sum(real_items.values()) or 1
    for item in sorted(set(real_items) - set(synthetic_items)):
        rare = real_items[item] / total < RARE_SHARE
        level = "note" if key in LISTED_NOTES or rare else "error"
        differences.append(Difference(level, where, f"{key}: the synthetic data lacks {item!r}"))
    return differences


def compare_columns(real: ColumnReport, synthetic: ColumnReport, where: str,
                    values_shown: bool) -> list[Difference]:
    """Compare two column reports.

    Args:
        real: The real column report.
        synthetic: The synthetic column report.
        where: Location for the messages.
        values_shown: Neither table is private, so listed values can be compared.

    Returns:
        The differences.
    """
    differences = []
    if real.name != synthetic.name:
        differences.append(Difference("error", where, f"name: real {real.name!r}, synthetic {synthetic.name!r}"))
    if real.kind != synthetic.kind:
        differences.append(Difference("error", where, f"kind: real {real.kind}, synthetic {synthetic.kind}"))
        return differences
    real_facts, synthetic_facts = fact_map(real.facts), fact_map(synthetic.facts)
    filled = int(real_facts.get("filled", "0"))
    if "cell types" in real_facts and "cell types" in synthetic_facts:
        differences += compare_cell_types(real_facts["cell types"], synthetic_facts["cell types"], filled, where)
    for key in sorted(set(real_facts) & set(synthetic_facts)):
        if key in LISTED_ERRORS | LISTED_NOTES and values_shown:
            differences += compare_listed(key, real_facts[key], synthetic_facts[key], where, filled)
        elif key in COLUMN_ERRORS | COLUMN_NOTES and real_facts[key] != synthetic_facts[key]:
            level = "error" if key in COLUMN_ERRORS else "note"
            differences.append(Difference(level, where, f"{key}: real {real_facts[key]}, "
                                                        f"synthetic {synthetic_facts[key]}"))
    return differences


def compare_tables(real: TableReport, synthetic: TableReport, where: str) -> list[Difference]:
    """Compare two table reports, then their columns by position.

    Args:
        real: The real table report.
        synthetic: The synthetic table report.
        where: Location for the messages.

    Returns:
        The differences.
    """
    differences = []
    if real.format != synthetic.format:
        differences.append(Difference("error", where, f"format: real {real.format}, synthetic {synthetic.format}"))
    if sheet_of(real.label) != sheet_of(synthetic.label):
        differences.append(Difference("error", where, f"sheet: real {sheet_of(real.label) or '-'}, "
                                                      f"synthetic {sheet_of(synthetic.label) or '-'}"))
    real_facts, synthetic_facts = fact_map(real.facts), fact_map(synthetic.facts)
    for key in sorted(set(real_facts) | set(synthetic_facts)):
        if real_facts.get(key) == synthetic_facts.get(key):
            continue
        level = "error" if key in TABLE_ERRORS else "note" if key in TABLE_NOTES else ""
        if level:
            differences.append(Difference(level, where, f"{key}: real {real_facts.get(key, '-')}, "
                                                        f"synthetic {synthetic_facts.get(key, '-')}"))
    values_shown = "private" not in real_facts and "private" not in synthetic_facts
    for index, (real_column, synthetic_column) in enumerate(zip(real.columns, synthetic.columns), start=1):
        column_where = f"{where} column {index} {real_column.name!r}"
        differences += compare_columns(real_column, synthetic_column, column_where, values_shown)
    for index, column in enumerate(real.columns[len(synthetic.columns):], start=len(synthetic.columns) + 1):
        differences.append(Difference("error", f"{where} column {index}", f"only the real data has {column.name!r}"))
    for index, column in enumerate(synthetic.columns[len(real.columns):], start=len(real.columns) + 1):
        differences.append(Difference("error", f"{where} column {index}",
                                      f"only the synthetic data has {column.name!r}"))
    return differences


def compare(real: Report, synthetic: Report) -> list[Difference]:
    """Compare a real report with a synthetic report, table by table in order.

    File labels differ by design, so tables pair by position. The description
    tables of a report of ``sdg report`` are left out.

    Args:
        real: The glimpse report of the real data.
        synthetic: The glimpse report of the synthetic output.

    Returns:
        The differences, in table and column order.
    """
    differences = []
    real_tables, synthetic_tables = glimpse_tables(real), glimpse_tables(synthetic)
    if len(real_tables) != len(synthetic_tables):
        differences.append(Difference("error", "report", f"tables: real {len(real_tables)}, "
                                                         f"synthetic {len(synthetic_tables)}"))
    for index, (real_table, synthetic_table) in enumerate(zip(real_tables, synthetic_tables), start=1):
        where = f"table {index} ({real_table.label} / {synthetic_table.label})"
        differences += compare_tables(real_table, synthetic_table, where)
    return differences
