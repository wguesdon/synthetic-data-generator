"""Measure the person values of each tool on the Synthea and Titanic tables.

The test checks the claim that sdg 0.11.0 has every capability of Faker on
real files: real-looking names, addresses, SSN and passport numbers, keys
that join two tables, and dates in order. Each check gives the share of the
synthetic cells that pass it, so 1.0 is best. The copy share gives the share
of the synthetic cells that equal a real cell, so 0.0 is best.

Columns pair by position, because the FakeDataR ``medium`` level renames them.

Usage:
    uv run python scripts/comparison/person_values.py RUN_DIR
"""

from __future__ import annotations

import csv
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pandas as pd

CASES = {"synthea_patients": Path("input/public/synthea_csv/patients.csv"),
         "synthea_encounters": Path("input/public/synthea_csv/encounters.csv"),
         "titanic": Path("input/public/titanic_csv/titanic.csv")}

#: The synthetic file of each tool and case, relative to the run folder.
FILES = {
    "sdg": {"synthea_patients": "sdg/synthea/data/file_2.csv", "synthea_encounters": "sdg/synthea/data/file_1.csv",
            "titanic": "sdg/titanic/data/file_1.csv"},
    "faker": {case: f"faker/{case}/synthetic.csv" for case in CASES},
    "fakedatar_low": {case: f"fakedatar_low/{case}/synthetic.csv" for case in CASES},
    "fakedatar_medium": {case: f"fakedatar_medium/{case}/synthetic.csv" for case in CASES},
}
MISSING = {"", "NA", "nan", "NaN"}
NAME = re.compile(r"[^\W\d_][\w'’ .,-]*", re.UNICODE)
TITANIC_NAME = re.compile(r"[^,]+, (Mr|Mrs|Miss|Master|Dr|Rev|Ms|Mme|Mlle|Major|Col|Capt|Don|Dona|Lady|Sir|Jonkheer|"
                          r"the Countess)\. .+")
ISO_Z = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


@dataclass(frozen=True)
class Check:
    """One check of one column.

    Attributes:
        case: The case.
        column: The real column name.
        name: What the check measures.
        measure: The function of the synthetic cells, the real cells and the tables.
    """

    case: str
    column: str
    name: str
    measure: Callable[[list[str], list[str]], float]


def filled(values: list[str]) -> list[str]:
    """Return the filled cells.

    Args:
        values: Cells as text.

    Returns:
        The cells that are not missing.
    """
    return [value for value in values if value.strip() not in MISSING]


def share(values: list[str], test: Callable[[str], bool]) -> float:
    """Return the share of the filled cells that pass a test.

    Args:
        values: Cells as text.
        test: The test of one cell.

    Returns:
        The share, or NaN with no filled cell.
    """
    cells = filled(values)
    return sum(map(test, cells)) / len(cells) if cells else float("nan")


def shape(value: str) -> str:
    """Return the shape of a cell: a letter becomes A and a digit becomes 9.

    Args:
        value: A cell.

    Returns:
        The shape.
    """
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", value))


def is_uuid(value: str) -> bool:
    """Say whether a cell is a UUID.

    Args:
        value: A cell.

    Returns:
        True for a UUID.
    """
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def is_name(value: str) -> bool:
    """Say whether a cell looks like a name: an upper-case first letter, then letters.

    Args:
        value: A cell.

    Returns:
        True for a name.
    """
    return bool(NAME.fullmatch(value)) and value[0].isupper() and sum(char.isalpha() for char in value) >= 2


def is_address(value: str) -> bool:
    """Say whether a cell looks like a street address: a number and three words or more.

    Args:
        value: A cell.

    Returns:
        True for an address.
    """
    return bool(re.search(r"\d", value)) and len(value.split()) >= 3 and value[0].isdigit()


def uuid_share(values: list[str], real: list[str]) -> float:
    """Share of cells that are UUIDs."""
    return share(values, is_uuid)


def name_share(values: list[str], real: list[str]) -> float:
    """Share of cells that look like names."""
    return share(values, is_name)


def distinct_share(values: list[str], real: list[str]) -> float:
    """Distinct synthetic values as a share of the distinct real values, at most 1."""
    return min(1.0, len(set(filled(values))) / max(1, len(set(filled(real)))))


def address_share(values: list[str], real: list[str]) -> float:
    """Share of cells that look like street addresses."""
    return share(values, is_address)


def has_real_shape(shapes: set[str], value: str) -> bool:
    """Say whether a cell has one of the real shapes.

    Args:
        shapes: The shapes of the real column.
        value: A cell.

    Returns:
        True when the shape of the cell is a real shape.
    """
    return shape(value) in shapes


def is_iso_z(value: str) -> bool:
    """Say whether a cell is a date and time in the ISO form with a Z."""
    return bool(ISO_Z.fullmatch(value))


def is_titanic_name(value: str) -> bool:
    """Say whether a cell has the form 'Last, Title. First' of the Titanic names."""
    return bool(TITANIC_NAME.fullmatch(value))


def shape_share(values: list[str], real: list[str]) -> float:
    """Share of cells whose shape is a shape of the real column.

    A tool that copies the mix of real shapes passes, as one that writes only
    the most common real shape does.
    """
    return share(values, partial(has_real_shape, set(map(shape, filled(real)))))


def iso_share(values: list[str], real: list[str]) -> float:
    """Share of cells in the ISO form with a Z, as the real cells."""
    return share(values, is_iso_z)


def titanic_share(values: list[str], real: list[str]) -> float:
    """Share of cells in the form 'Last, Title. First' of the Titanic names."""
    return share(values, is_titanic_name)


def copy_share(values: list[str], real: list[str]) -> float:
    """Share of filled cells that equal a real cell."""
    return share(values, set(filled(real)).__contains__)


CHECKS = [
    Check("synthea_patients", "Id", "UUID", uuid_share),
    Check("synthea_patients", "SSN", "a real shape, 999-99-9999", shape_share),
    Check("synthea_patients", "DRIVERS", "a real shape", shape_share),
    Check("synthea_patients", "PASSPORT", "a real shape", shape_share),
    Check("synthea_patients", "FIRST", "looks like a name", name_share),
    Check("synthea_patients", "FIRST", "distinct names, share of real", distinct_share),
    Check("synthea_patients", "LAST", "looks like a name", name_share),
    Check("synthea_patients", "ADDRESS", "looks like a street address", address_share),
    Check("synthea_patients", "CITY", "looks like a name", name_share),
    Check("synthea_patients", "ZIP", "a real shape, 99999", shape_share),
    Check("synthea_encounters", "Id", "UUID", uuid_share),
    Check("synthea_encounters", "START", "ISO form with Z", iso_share),
    Check("titanic", "Name", "looks like a name", name_share),
    Check("titanic", "Name", "form 'Last, Title. First'", titanic_share),
    Check("titanic", "Ticket", "a real shape", shape_share),
    Check("titanic", "Cabin", "a real shape", shape_share),
]

#: Pairs of date columns where the second date comes on or after the first.
ORDERS = [("synthea_encounters", "START", "STOP"), ("synthea_patients", "BIRTHDATE", "DEATHDATE")]

#: Columns whose copy share the privacy table gives.
COPY_COLUMNS = [("synthea_patients", "Id"), ("synthea_patients", "SSN"), ("synthea_patients", "PASSPORT"),
                ("synthea_patients", "FIRST"), ("synthea_patients", "LAST"), ("synthea_patients", "ADDRESS"),
                ("synthea_patients", "CITY"), ("synthea_patients", "ZIP"), ("titanic", "Name"),
                ("titanic", "Ticket")]


def read(path: Path) -> pd.DataFrame:
    """Read a CSV file with every cell as text.

    Args:
        path: The file.

    Returns:
        The table.
    """
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def column(table: pd.DataFrame, real: pd.DataFrame, name: str) -> list[str]:
    """Return the synthetic column at the position of a real column.

    Args:
        table: The synthetic table.
        real: The real table.
        name: The real column name.

    Returns:
        The cells.
    """
    return table.iloc[:, real.columns.get_loc(name)].tolist()


def order_share(table: pd.DataFrame, real: pd.DataFrame, first: str, second: str) -> float:
    """Share of rows where the second date comes on or after the first.

    Args:
        table: The synthetic table.
        real: The real table, for the positions.
        first: The earlier real column.
        second: The later real column.

    Returns:
        The share of the rows with both dates.
    """
    start = pd.to_datetime(pd.Series(column(table, real, first)), errors="coerce", utc=True, format="mixed")
    end = pd.to_datetime(pd.Series(column(table, real, second)), errors="coerce", utc=True, format="mixed")
    both = start.notna() & end.notna()
    return float((end[both] >= start[both]).mean()) if both.any() else float("nan")


def main() -> int:
    """Measure every tool and write the tables.

    Returns:
        The exit code.
    """
    run = Path(sys.argv[1])
    reals = {case: read(path) for case, path in CASES.items()}
    tables = {tool: {case: read(run / path) for case, path in files.items() if (run / path).exists()}
              for tool, files in FILES.items()}
    rows = []
    for check in CHECKS:
        real = reals[check.case]
        row = [f"{check.case} {check.column}", check.name]
        for tool in FILES:
            table = tables[tool].get(check.case)
            row.append(check.measure(column(table, real, check.column), real[check.column].tolist())
                       if table is not None else float("nan"))
        rows.append(row)
    for case, first, second in ORDERS:
        rows.append([f"{case} {second}", f"on or after {first}",
                     *[order_share(tables[tool][case], reals[case], first, second) if case in tables[tool]
                       else float("nan") for tool in FILES]])
    links = ["synthea_encounters PATIENT", "a key of the synthetic patients"]
    for tool in FILES:
        encounters, patients = tables[tool].get("synthea_encounters"), tables[tool].get("synthea_patients")
        if encounters is None or patients is None:
            links.append(float("nan"))
            continue
        keys = set(column(patients, reals["synthea_patients"], "Id"))
        links.append(share(column(encounters, reals["synthea_encounters"], "PATIENT"), keys.__contains__))
    rows.append(links)
    copies = []
    for case, name in COPY_COLUMNS:
        real = reals[case]
        copies.append([f"{case} {name}", "copy share", *[
            copy_share(column(tables[tool][case], real, name), real[name].tolist()) if case in tables[tool]
            else float("nan") for tool in FILES]])
    header = ["Column", "Check", *FILES]
    lines = ["| " + " | ".join(header) + " |", "|---" * len(header) + "|"]
    for row in rows + copies:
        lines.append("| " + " | ".join(row[:2] + [f"{value:.2f}" for value in row[2:]]) + " |")
    (run / "person_values.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (run / "person_values.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        writer.writerows(rows + copies)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
