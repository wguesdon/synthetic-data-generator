"""The glimpse report: its data model, its text form and its parser.

A report is a list of tables. Each table has facts, such as its row count, and
columns. Each column has a name, a kind and facts. The text form is what a
person reads and pastes into an agent session, so it must stay readable; the
parser reads the same text back, so ``sdg compare`` can work on two pasted
reports. ``parse(render(report))`` gives the report back unchanged.

The text form::

    # sdg glimpse 0.1.0
    == file_1.csv <csv>
    rows: 120; columns: 2; delimiter: ','
    $ 'subject_id' <identifier> filled: 120; empty: 0; shapes: 'AA-9999' 120
    $ 'visit' <categorical> filled: 120; empty: 0; values: 'visit_1' 60, 'visit_2 ' 60
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field

FACT_SEPARATOR = "; "
STRING_LITERAL = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
TABLE_LINE = re.compile(r"^== (?P<label>.*) <(?P<format>[\w-]+)>$")
COLUMN_LINE = re.compile(rf"^\$ (?P<name>{STRING_LITERAL}) <(?P<kind>[\w-]+)>(?: (?P<facts>.*))?$")
COUNTED_ITEM = re.compile(rf"^(?P<item>{STRING_LITERAL}|\S+) (?P<count>\d+)$")
MORE_ITEMS = re.compile(r"^and (?P<count>\d+) more$")

#: Formats of the tables of ``sdg describe``. A report of ``sdg report`` holds
#: them after the glimpse tables, and ``sdg compare`` leaves them out.
DESCRIPTION_FORMATS = frozenset({"design", "groups"})


@dataclass
class Fact:
    """One reported fact, such as ``rows: 120``.

    Attributes:
        key: Name of the fact.
        value: Value of the fact, as printed.
    """

    key: str
    value: str


@dataclass
class ColumnReport:
    """The report of one column, or of one channel or field of a file.

    Attributes:
        name: Column name.
        kind: Column kind, such as ``numeric``.
        facts: Facts in print order.
    """

    name: str
    kind: str
    facts: list[Fact] = field(default_factory=list)


@dataclass
class TableReport:
    """The report of one table, sheet or file.

    Attributes:
        label: Generic file label, with the sheet for a workbook.
        format: Format name, such as ``csv`` or ``fastq``.
        facts: Table facts in print order.
        columns: Column reports in file order.
    """

    label: str
    format: str
    facts: list[Fact] = field(default_factory=list)
    columns: list[ColumnReport] = field(default_factory=list)


@dataclass
class Report:
    """A complete glimpse report.

    Attributes:
        tables: Table reports in file order.
        comments: Comment lines without their ``#``.
    """

    tables: list[TableReport] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)


def counted(counts: Counter, limit: int, quote: bool = True) -> str:
    """Write the most common items of a counter on one line.

    Args:
        counts: Items and their counts.
        limit: Most items to write.
        quote: Write each item as ``repr``. Set False for plain words such as
            cell type names.

    Returns:
        The items with their counts, and how many were left out.
    """
    shown = ", ".join(f"{item!r} {count}" if quote else f"{item} {count}"
                      for item, count in counts.most_common(limit))
    rest = len(counts) - limit
    return shown + (f", and {rest} more" if rest > 0 else "")


def split_outside_quotes(text: str, separator: str) -> list[str]:
    """Split a text on a separator that is not inside a quoted string.

    Args:
        text: The text.
        separator: The separator, such as ``"; "``.

    Returns:
        The parts, in order.
    """
    parts, start, quote, index = [], 0, "", 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif text.startswith(separator, index):
            parts.append(text[start:index])
            start = index + len(separator)
            index = start
            continue
        index += 1
    parts.append(text[start:])
    return parts


def parse_counted(value: str) -> tuple[dict[str, int], int]:
    """Read back a value written by :func:`counted`.

    Args:
        value: Text such as ``'a' 3, 'b' 2, and 4 more`` or ``str 120``.

    Returns:
        The listed items with their counts, and the number of items left out.
    """
    items: dict[str, int] = {}
    more = 0
    for part in split_outside_quotes(value, ", "):
        part = part.strip()
        if not part:
            continue
        if match := MORE_ITEMS.match(part):
            more = int(match["count"])
        elif match := COUNTED_ITEM.match(part):
            item = match["item"]
            items[ast.literal_eval(item) if item[0] in "'\"" else item] = int(match["count"])
    return items, more


def render_facts(facts: list[Fact]) -> str:
    """Join facts as ``key: value`` pairs.

    Args:
        facts: The facts.

    Returns:
        One line of facts.
    """
    return FACT_SEPARATOR.join(f"{fact.key}: {fact.value}" for fact in facts)


def parse_facts(text: str) -> list[Fact]:
    """Read back a line written by :func:`render_facts`.

    Args:
        text: One line of facts.

    Returns:
        The facts, in order.
    """
    facts = []
    for part in split_outside_quotes(text, FACT_SEPARATOR):
        key, _, value = part.partition(": ")
        facts.append(Fact(key, value))
    return facts


def render(report: Report) -> str:
    """Write a report as text.

    Args:
        report: The report.

    Returns:
        The text form, ending with a newline.
    """
    lines = [f"# {comment}" for comment in report.comments]
    for table in report.tables:
        lines.append(f"== {table.label} <{table.format}>")
        if table.facts:
            lines.append(render_facts(table.facts))
        for column in table.columns:
            line = f"$ {column.name!r} <{column.kind}>"
            lines.append(line + (f" {render_facts(column.facts)}" if column.facts else ""))
    return "\n".join(lines) + "\n"


def parse(text: str) -> Report:
    """Read a report from its text form.

    Lines outside the known forms are skipped, so a report pasted with extra
    lines around it still parses.

    Args:
        text: The text form of a report.

    Returns:
        The report.
    """
    report = Report()
    table: TableReport | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("#"):
            report.comments.append(line[1:].strip())
        elif match := TABLE_LINE.match(line):
            table = TableReport(match["label"], match["format"])
            report.tables.append(table)
        elif table is None or not line.strip():
            continue
        elif match := COLUMN_LINE.match(line):
            facts = parse_facts(match["facts"]) if match["facts"] else []
            table.columns.append(ColumnReport(ast.literal_eval(match["name"]), match["kind"], facts))
        elif not table.columns:
            table.facts.extend(parse_facts(line))
    return report


def glimpse_tables(report: Report) -> list[TableReport]:
    """Return the glimpse tables of a report, without the description tables.

    Args:
        report: A glimpse report, or the joined report of ``sdg report``.

    Returns:
        The tables whose format is not in :data:`DESCRIPTION_FORMATS`.
    """
    return [table for table in report.tables if table.format not in DESCRIPTION_FORMATS]


def fact_map(facts: list[Fact]) -> dict[str, str]:
    """Index facts by key.

    Args:
        facts: The facts.

    Returns:
        Each key mapped to its value. A repeated key keeps its last value.
    """
    return {fact.key: fact.value for fact in facts}
