"""``sdg init --from-schema``: a config from a database schema with no rows.

A schema holds the tables, the columns and their types, and no value. So the
config gets each column with its kind and default values, which a person or an
agent then edits. A primary key and a foreign key give identifier columns, and
a foreign key gives a ``link`` to the key that it references, so a join of the
synthetic tables finds each key.

The schema is a SQLite database file or a text of ``CREATE TABLE`` statements.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sdg import fake_values, privacy
from sdg.config import SCHEMA_VERSION, parse_config
from sdg.version import SDG_VERSION

#: File suffixes of a SQLite database.
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")

#: Words that end the type of a column definition.
CONSTRAINT_WORDS = {"PRIMARY", "NOT", "NULL", "DEFAULT", "REFERENCES", "UNIQUE", "CHECK", "COLLATE", "GENERATED",
                    "CONSTRAINT", "AUTO_INCREMENT", "AUTOINCREMENT", "IDENTITY", "ON", "COMMENT"}

#: Words that start a table constraint instead of a column.
TABLE_CONSTRAINTS = ("PRIMARY", "FOREIGN", "CONSTRAINT", "UNIQUE", "CHECK", "INDEX", "KEY", "EXCLUDE")

CREATE_TABLE = re.compile(r"CREATE\s+(?:TEMP\w*\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)\s*\(", re.IGNORECASE)
REFERENCES = re.compile(r"REFERENCES\s+([^\s(]+)\s*\(\s*([^)\s]+)\s*\)", re.IGNORECASE)
FOREIGN_KEY = re.compile(r"FOREIGN\s+KEY\s*\(\s*([^)\s]+)\s*\)\s*REFERENCES\s+([^\s(]+)\s*\(\s*([^)\s]+)\s*\)",
                         re.IGNORECASE)
PRIMARY_KEY = re.compile(r"PRIMARY\s+KEY\s*\(([^)]*)\)", re.IGNORECASE)
SCALE = re.compile(r"\(\s*\d+\s*,\s*(\d+)\s*\)")
LENGTH = re.compile(r"\(\s*(\d+)\s*\)")

#: Rows of each table when the command gets no row count.
DEFAULT_ROWS = 100


@dataclass
class SchemaColumn:
    """One column of a schema.

    Attributes:
        name: Column name.
        type: Declared type, such as ``VARCHAR(40)``.
        primary_key: The column is the primary key of its table.
        references: The table and the column that a foreign key references.
    """

    name: str
    type: str
    primary_key: bool = False
    references: tuple[str, str] | None = None


@dataclass
class SchemaTable:
    """One table of a schema.

    Attributes:
        name: Table name.
        columns: The columns in order.
    """

    name: str
    columns: list[SchemaColumn]


def unquote(name: str) -> str:
    """Remove the quotes of an SQL name.

    Args:
        name: A name, such as ``"Patient Id"``, ``[id]`` or ``main.visits``.

    Returns:
        The bare name, without a schema prefix.
    """
    name = name.strip().rstrip(";")
    name = name.split(".")[-1] if not name.startswith(("\"", "`", "[")) else name
    return name.strip("\"`[]")


def read_sqlite(path: Path) -> list[SchemaTable]:
    """Read the schema of a SQLite database, which may hold no row.

    Args:
        path: The database file. It opens read-only.

    Returns:
        Each table with its columns, keys and foreign keys.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
        tables = []
        for name in names:
            quoted = name.replace('"', '""')
            references = {row[3]: (row[2], row[4]) for row in connection.execute(
                f'PRAGMA foreign_key_list("{quoted}")')}
            columns = [SchemaColumn(row[1], row[2] or "", bool(row[5]), references.get(row[1]))
                       for row in connection.execute(f'PRAGMA table_info("{quoted}")')]
            tables.append(SchemaTable(name, columns))
        return tables
    finally:
        connection.close()


def split_items(body: str) -> list[str]:
    """Split the body of a ``CREATE TABLE`` statement at its top-level commas.

    Args:
        body: The text between the outer parentheses.

    Returns:
        The column definitions and the table constraints.
    """
    items, depth, current = [], 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    items.append("".join(current).strip())
    return [item for item in items if item]


def table_body(text: str, start: int) -> str:
    """Return the text between the parenthesis at a position and its closing parenthesis.

    Args:
        text: The SQL text.
        start: Position after the opening parenthesis.

    Returns:
        The body.
    """
    depth = 1
    for index in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[index], 0)
        if depth == 0:
            return text[start:index]
    return text[start:]


def column_definition(item: str) -> SchemaColumn:
    """Read one column definition, such as ``visit_id INTEGER PRIMARY KEY``.

    Args:
        item: The definition.

    Returns:
        The column.
    """
    match = re.match(r'\s*("[^"]+"|`[^`]+`|\[[^\]]+\]|\S+)\s*(.*)', item, re.DOTALL)
    name, rest = unquote(match.group(1)), match.group(2)
    words = []
    for word in rest.split():
        if word.upper().strip(",") in CONSTRAINT_WORDS:
            break
        words.append(word)
    reference = REFERENCES.search(rest)
    return SchemaColumn(name, " ".join(words), bool(re.search(r"PRIMARY\s+KEY", rest, re.IGNORECASE)),
                        (unquote(reference.group(1)), unquote(reference.group(2))) if reference else None)


def read_sql(text: str) -> list[SchemaTable]:
    """Read the ``CREATE TABLE`` statements of an SQL text.

    Args:
        text: The SQL text. Comments are removed first.

    Returns:
        Each table with its columns, keys and foreign keys.
    """
    text = re.sub(r"--[^\n]*|/\*.*?\*/", "", text, flags=re.DOTALL)
    tables = []
    for match in CREATE_TABLE.finditer(text):
        items = split_items(table_body(text, match.end()))
        constraints = [item for item in items if item.split()[0].upper() in TABLE_CONSTRAINTS]
        columns = [column_definition(item) for item in items if item not in constraints]
        by_name = {column.name: column for column in columns}
        for item in constraints:
            if (key := PRIMARY_KEY.search(item)) and "FOREIGN" not in item.upper():
                for name in key.group(1).split(","):
                    if unquote(name) in by_name:
                        by_name[unquote(name)].primary_key = True
            if foreign := FOREIGN_KEY.search(item):
                column = by_name.get(unquote(foreign.group(1)))
                if column is not None:
                    column.references = (unquote(foreign.group(2)), unquote(foreign.group(3)))
        tables.append(SchemaTable(unquote(match.group(1)), columns))
    return tables


def read_schema(path: Path) -> list[SchemaTable]:
    """Read a schema from a SQLite file or an SQL text.

    Args:
        path: The schema file.

    Returns:
        The tables.

    Raises:
        ValueError: If the file holds no table.
    """
    tables = read_sqlite(path) if privacy.file_suffix(path) in SQLITE_SUFFIXES else read_sql(
        path.read_text(encoding="utf-8"))
    if not tables:
        raise ValueError(f"{path.name}: no table in the schema")
    return tables


def is_key(column: SchemaColumn) -> bool:
    """Say whether a column holds keys.

    Args:
        column: The column.

    Returns:
        True for a primary key, a foreign key or a name that marks an identifier.
    """
    return column.primary_key or column.references is not None or privacy.is_identifier_name(column.name)


def key_fields(column: SchemaColumn) -> dict[str, Any]:
    """Return the fields of an identifier column from its type.

    Args:
        column: The key column.

    Returns:
        The kind and the pattern: random UUIDs for a UUID type, digits for an
        integer type, else letters and digits.
    """
    declared = column.type.upper()
    if "UUID" in declared or "UNIQUEIDENTIFIER" in declared:
        return {"kind": "identifier", "pattern": "uuid"}
    if "INT" in declared:
        return {"kind": "identifier", "pattern": "99999"}
    return {"kind": "identifier", "pattern": "AAA-99999"}


def value_fields(column: SchemaColumn) -> dict[str, Any]:
    """Return the fields of a column that holds values, from its type.

    Args:
        column: The column.

    Returns:
        The kind and default fields of the column.
    """
    declared = column.type.upper()
    if "BOOL" in declared or declared == "BIT":
        return {"kind": "boolean", "true_share": 0.5}
    if any(word in declared for word in ("TIMESTAMP", "DATETIME")):
        return {"kind": "date", "interval_days": [0.0, 365.0], "date_format": "%Y-%m-%d %H:%M:%S"}
    if "DATE" in declared:
        return {"kind": "date", "interval_days": [0.0, 365.0]}
    if "INT" in declared:
        return {"kind": "numeric", "quantiles": [0.0, 100.0], "decimals": 0}
    if any(word in declared for word in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL", "MONEY")):
        scale = SCALE.search(declared)
        return {"kind": "numeric", "quantiles": [0.0, 100.0], "decimals": int(scale.group(1)) if scale else 2}
    length = LENGTH.search(declared)
    longest = min(int(length.group(1)), 40) if length else 20
    fields: dict[str, Any] = {"kind": "text", "lengths": [1, max(1, longest)]}
    provider = fake_values.provider_for(column.name)
    if provider:
        fields["provider"] = provider
    return fields


def dataset_name(name: str) -> str:
    """Make a dataset name and a file stem from a table name.

    Args:
        name: The table name.

    Returns:
        The name, with each character that is not a letter, a digit or ``_`` replaced.
    """
    return re.sub(r"\W+", "_", name).strip("_") or "table"


def resolved_reference(column: SchemaColumn, tables: list[SchemaTable]) -> tuple[str, str] | None:
    """Return the table and the column that a foreign key references.

    A SQLite foreign key that names no column references the primary key of
    the table.

    Args:
        column: The column.
        tables: Every table of the schema.

    Returns:
        The table and the column, or None.
    """
    if column.references is None:
        return None
    table, name = column.references
    if name:
        return table, name
    target = next((item for item in tables if item.name == table), None)
    key = next((item.name for item in target.columns if item.primary_key), None) if target else None
    return (table, key) if key else None


def schema_config(tables: list[SchemaTable], rows: int, seed: int) -> dict[str, Any]:
    """Build a config with one CSV dataset per table.

    Args:
        tables: The tables of the schema.
        rows: Rows of each table.
        seed: Seed of the config.

    Returns:
        The config, which validates.
    """
    names = {table.name: dataset_name(table.name) for table in tables}
    keys = {(table.name, column.name) for table in tables for column in table.columns if is_key(column)}
    datasets = []
    for table in tables:
        columns = []
        for column in table.columns:
            fields = key_fields(column) if is_key(column) else value_fields(column)
            parent = resolved_reference(column, tables)
            if parent and parent[0] in names and parent in keys and parent[0] != table.name:
                target = next(item for item in tables if item.name == parent[0])
                linked = next((item for item in target.columns if item.name == parent[1]), None)
                if linked is not None and linked.references is None:
                    fields["link"] = {"dataset": names[parent[0]], "column": parent[1]}
            columns.append({"name": column.name, **fields})
        datasets.append({"name": names[table.name], "format": "csv", "file": f"{names[table.name]}.csv",
                         "sheets": [{"rows": rows, "columns": columns}]})
    config = {"version": SCHEMA_VERSION, "seed": seed, "mode": "resample", "datasets": datasets}
    parse_config(config)
    return config


def from_schema(path: Path, rows: int = DEFAULT_ROWS, seed: int = 0) -> str:
    """Write the config of a database schema as commented YAML.

    Args:
        path: The schema file: SQLite, or SQL text.
        rows: Rows of each table.
        seed: Seed of the config.

    Returns:
        The YAML text, with a header that says that each value is a default.
    """
    config = schema_config(read_schema(path), rows, seed)
    header = [f"# Written by sdg init --from-schema {SDG_VERSION} on {dt.date.today().isoformat()}.",
              "# The schema holds no value, so each row count, quantile, length, interval and share",
              "# is a default. Edit them, and mark each value that you keep as an assumption."]
    body = yaml.safe_dump(config, sort_keys=False, allow_unicode=True, default_flow_style=None, width=100)
    return "\n".join(header) + "\n" + body
