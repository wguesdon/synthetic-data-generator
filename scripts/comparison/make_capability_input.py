"""Write the invented input files of the capability checks.

Every value is invented with a fixed seed. No file holds real data. The
files give each check a known fact, such as a correlation of 0.8 between two
columns, an exact missing share or a key link between two tables.

Usage:
    uv run python scripts/comparison/make_capability_input.py OUT_DIR
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

ROWS = 200
SEED = 7
FIRST_NAMES = ["anna", "ben", "chloe", "david", "emma", "felix", "grace", "hugo", "iris", "jonas"]


def visits(rng: np.random.Generator) -> pd.DataFrame:
    """Build the invented visits table, which has no private column.

    Args:
        rng: The random generator.

    Returns:
        The table, with an identifier, categories, numbers, tokens, dates,
        booleans, codes, emails, percent texts, a correlated pair and a date
        pair.
    """
    start = dt.date(2023, 1, 1)
    x = rng.normal(0.0, 1.0, ROWS)
    y = 0.8 * x + 0.6 * rng.normal(0.0, 1.0, ROWS)
    crp = np.round(np.exp(rng.normal(0.0, 1.0, ROWS)), 4).astype(object)
    crp[rng.choice(ROWS, size=20, replace=False)] = None
    il6 = np.round(rng.uniform(1.0, 9.0, ROWS), 2).astype(object)
    il6[rng.choice(ROWS, size=30, replace=False)] = "<LOD"
    admit = [start + dt.timedelta(days=int(day)) for day in rng.integers(0, 300, ROWS)]
    stay = rng.integers(0, 15, ROWS)
    return pd.DataFrame({
        "patient_id": [f"A{1000 + 7 * index}" for index in range(ROWS)],
        "arm": ["placebo"] * 100 + ["drug_a"] * 60 + ["drug_b"] * 40,
        "crp": crp,
        "il6": il6,
        "visit_date": [(start + dt.timedelta(days=int(day))).isoformat() for day in rng.integers(0, 365, ROWS)],
        "fasting": np.where(rng.random(ROWS) < 0.3, "TRUE", "FALSE"),
        "ward": [f"{'KLMNP'[code // 90]}{10 + code % 90}" for code in rng.choice(450, ROWS, replace=False)],
        "contact": [f"{FIRST_NAMES[index % 10]}.{index}@clinic-example.org" for index in range(ROWS)],
        "score_pct": [f"{int(value)}%" for value in rng.integers(40, 100, ROWS)],
        "x": np.round(x, 4),
        "y": np.round(y, 4),
        "admit_date": [day.isoformat() for day in admit],
        "discharge_date": [(day + dt.timedelta(days=int(extra))).isoformat() for day, extra in zip(admit, stay,
                                                                                                    strict=True)],
    })


def demographics(rng: np.random.Generator) -> pd.DataFrame:
    """Build the invented demographics table, which is private by its age and sex columns.

    Args:
        rng: The random generator.

    Returns:
        The table.
    """
    return pd.DataFrame({
        "patient_id": [f"A{1000 + 7 * index}" for index in range(ROWS)],
        "age": rng.integers(18, 91, ROWS),
        "sex": rng.choice(["F", "M"], ROWS),
        "arm": ["placebo"] * 100 + ["drug_a"] * 60 + ["drug_b"] * 40,
        "weight_kg": np.round(rng.normal(75.0, 12.0, ROWS), 1),
    })


def write_titled(table: pd.DataFrame, path: Path) -> None:
    """Write a CSV file with a title row and a comment line above the header.

    Args:
        table: The visits table.
        path: The output file.
    """
    part = table[["patient_id", "arm", "crp", "fasting"]].head(60)
    text = "Visit export of the invented study\n# exported for the capability checks\n" + part.to_csv(index=False)
    path.write_text(text, encoding="utf-8")


def write_workbook(table: pd.DataFrame, path: Path) -> None:
    """Write a workbook with two sheets.

    Args:
        table: The visits table.
        path: The output file.
    """
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "visits"
    first.append(["patient_id", "arm", "crp"])
    for row in table[["patient_id", "arm", "crp"]].itertuples(index=False):
        first.append(list(row))
    second = workbook.create_sheet("labs")
    second.append(["sample", "il6", "ward"])
    for row in table[["patient_id", "il6", "ward"]].itertuples(index=False):
        second.append(list(row))
    workbook.save(path)


def write_link(rng: np.random.Generator, out: Path) -> None:
    """Write a patient table and an admission table that refers to it.

    Args:
        rng: The random generator.
        out: The output folder.
    """
    patients = pd.DataFrame({"patient_id": [f"A{1000 + 7 * index}" for index in range(80)],
                             "arm": rng.choice(["placebo", "drug_a"], 80)})
    patients.to_csv(out / "patients.csv", index=False)
    admissions = pd.DataFrame({"admission_id": [f"V{50000 + index}" for index in range(240)],
                               "patient_id": rng.choice(patients["patient_id"], 240),
                               "length_of_stay": rng.integers(1, 20, 240)})
    admissions.to_csv(out / "admissions.csv", index=False)


def write_schema(path: Path) -> None:
    """Write a SQLite database with one table and no rows.

    Args:
        path: The database file.
    """
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE visits (patient_id TEXT, age INTEGER, crp REAL, visit_date DATE, "
                           "fasting BOOLEAN)")


def main() -> int:
    """Write every input file.

    Returns:
        The exit code.
    """
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    table = visits(rng)
    table.to_csv(out / "visits.csv", index=False)
    demographics(rng).to_csv(out / "demographics.csv", index=False)
    typed = table[["patient_id", "arm", "crp", "visit_date", "fasting", "ward"]].assign(
        crp=pd.to_numeric(table["crp"]), visit_date=pd.to_datetime(table["visit_date"]).dt.date,
        fasting=table["fasting"] == "TRUE")
    typed.to_parquet(out / "visits.parquet", index=False)
    write_titled(table, out / "titled.csv")
    write_workbook(table, out / "workbook.xlsx")
    write_link(rng, out)
    schema = out / "schema.sqlite"
    if not schema.exists():
        write_schema(schema)
    print(f"Wrote the capability input to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
