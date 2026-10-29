# /// script
# requires-python = ">=3.13"
# dependencies = ["faker==40.43.0", "pandas>=2.2"]
# ///
"""Make a Faker table for each comparison case.

Faker reads no data, so a Faker user writes one provider call per column. This
script plays that user. It reads the real table on the secure machine, and it
picks a provider for each column from the column name and the cell values:

* A column with 20 distinct values or less draws its real values with their
  real shares, through ``random_element``.
* A column of person names, emails, phone numbers or addresses gets the
  matching Faker provider.
* A number column draws a uniform number between the real minimum and maximum:
  an integer when each real value is whole, else a number with the usual
  decimal places of the column.
* A date column draws a date between the real first and last dates, in the real
  format.
* A code column whose most common shape fills half of the cells, such as
  ``N1A1``, gets ``bothify`` with that shape.
* Other text gets Faker text of the real maximum length.

Each column keeps its real missing share. The mapping is the work of this
comparison, not a part of Faker.

Usage:
    uv run scripts/comparison/run_faker.py RUN_DIR [CASES]

CASES is a tab-separated case list, by default ``scripts/comparison/cases.txt``.
"""

from __future__ import annotations

import csv
import random
import re
import string
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import pandas as pd
from faker import Faker

SEED = 42
CATEGORY_LIMIT = 20
MISSING = {"", "NA", "N/A", "NaN", "nan", "NULL", "null", "None", "#N/A"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?Z?")
PERSON_NAME = re.compile(r"(^|[_\s])(full_?)?name$|^name$", re.IGNORECASE)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: Words of a column name and the Faker provider that a user picks for them, first match first.
PERSON_WORDS = (
    (("email",), "email"), (("phone", "mobile"), "phone_number"), (("first",), "first_name"),
    (("last", "surname", "maiden"), "last_name"), (("address", "street"), "street_address"),
    (("city",), "city"), (("county",), "city"), (("country",), "country"), (("zip", "postcode"), "postcode"),
    (("ssn",), "ssn"), (("company",), "company"), (("website", "url"), "url"), (("passport",), "passport_number"),
)


def read_cases(path: Path) -> list[tuple[str, Path]]:
    """Read the case list.

    Args:
        path: The tab-separated case file.

    Returns:
        Each case name with its real file.
    """
    with path.open(newline="") as handle:
        return [(row["case"], Path(row["file"])) for row in csv.DictReader(handle, delimiter="\t")]


def decimals(values: list[str]) -> int:
    """Count the usual decimal places among number cells.

    The most common count wins, because a few cells of the penguin file carry
    float noise, such as ``8.3945900000000009``.

    Args:
        values: Number cells as text.

    Returns:
        The most common count of digits after the point, 0 for integers.
    """
    places = Counter(len(value.split(".")[1]) if "." in value else 0 for value in values)
    return places.most_common(1)[0][0]


def bothify_shape(values: list[str]) -> tuple[str, int]:
    """Return the most common shape of the values in the bothify form.

    Args:
        values: Text cells.

    Returns:
        The shape, with ``?`` for a letter and ``#`` for a digit, and its count.
    """
    shapes = Counter(re.sub(r"[A-Za-z]", "?", re.sub(r"\d", "#", value)) for value in values)
    return shapes.most_common(1)[0]


def person_kind(name: str, values: list[str]) -> str | None:
    """Pick a person provider from the column name, when the values agree.

    A Faker user who looks at the column picks the matching provider, such as
    ``first_name`` for a column of first names or ``uuid4`` for a column of UUIDs.

    Args:
        name: Column name.
        values: Filled cells.

    Returns:
        A Faker provider name, or None.
    """
    lower = name.lower()
    if all(UUID.fullmatch(value) for value in values):
        return "uuid4"
    for words, provider in PERSON_WORDS:
        if any(word in lower for word in words):
            return provider
    if PERSON_NAME.search(name) and sum(" " in value for value in values) > len(values) / 2:
        return "name"
    return None


def fake_values(fake: Faker, name: str, values: list[str], rows: int) -> list[str]:
    """Draw the filled cells of one column.

    Args:
        fake: The seeded Faker instance.
        name: Column name.
        values: Filled cells of the real column.
        rows: Number of cells to draw.

    Returns:
        The drawn cells as text.
    """
    counts = Counter(values)
    if len(counts) <= CATEGORY_LIMIT:
        weights = OrderedDict((value, count / len(values)) for value, count in counts.most_common())
        return [str(fake.random_element(weights)) for _ in range(rows)]
    provider = person_kind(name, values)
    if provider:
        return [str(getattr(fake, provider)()).replace("\n", ", ") for _ in range(rows)]
    numbers = pd.to_numeric(pd.Series(values), errors="coerce")
    if numbers.notna().all():
        low, high = float(numbers.min()), float(numbers.max())
        if (numbers % 1 == 0).all():
            return [str(fake.random_int(int(low), int(high))) for _ in range(rows)]
        places = max(decimals([value for value in values if "." in value]), 1)
        return [f"{fake.pyfloat(min_value=low, max_value=high, right_digits=places):.{places}f}"
                for _ in range(rows)]
    if all(DATETIME.fullmatch(value) for value in values):
        stamps = pd.to_datetime(pd.Series(values))
        form = "%Y-%m-%d %H:%M:%S" if values[0].count(":") == 2 else "%Y-%m-%d %H:%M"
        if "T" in values[0]:
            form = form.replace(" ", "T") + ("Z" if values[0].endswith("Z") else "")
        return [fake.date_time_between(stamps.min(), stamps.max()).strftime(form) for _ in range(rows)]
    if all(DATE.fullmatch(value) for value in values):
        stamps = pd.to_datetime(pd.Series(values))
        return [fake.date_between(stamps.min().date(), stamps.max().date()).isoformat() for _ in range(rows)]
    shape, count = bothify_shape(values)
    if count >= len(values) / 2 and len(shape) <= 12:
        letters = string.ascii_uppercase if "".join(values).isupper() else string.ascii_letters
        return [fake.bothify(shape, letters=letters) for _ in range(rows)]
    longest = max(map(len, values))
    return [fake.text(max_nb_chars=max(longest, 5)).rstrip(".")[:longest] for _ in range(rows)]


def fake_table(real: pd.DataFrame, fake: Faker, rng: random.Random) -> pd.DataFrame:
    """Make a Faker table with the columns, the row count and the missing shares of a real table.

    Args:
        real: The real table, read as text.
        fake: The seeded Faker instance.
        rng: The seeded generator of the missing cells.

    Returns:
        The synthetic table, as text.
    """
    rows = len(real)
    columns = {}
    for name in real.columns:
        cells = real[name].tolist()
        values = [cell for cell in cells if cell not in MISSING]
        share = 1 - len(values) / rows
        drawn = fake_values(fake, name, values, rows) if values else [""] * rows
        columns[name] = ["" if rng.random() < share else cell for cell in drawn]
    return pd.DataFrame(columns, columns=real.columns)


def main() -> int:
    """Write one Faker table for each case.

    Returns:
        The exit code.
    """
    run_dir = Path(sys.argv[1])
    cases = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("scripts/comparison/cases.txt")
    for case, path in read_cases(cases):
        Faker.seed(SEED)
        fake = Faker("en_US")
        rng = random.Random(SEED)
        real = pd.read_csv(path, dtype=str, keep_default_na=False)
        table = fake_table(real, fake, rng)
        out = run_dir / "faker" / case
        (out / "shared").mkdir(parents=True, exist_ok=True)
        table.to_csv(out / "synthetic.csv", index=False)
        table.to_csv(out / "shared" / "synthetic.csv", index=False)
        print(f"faker {case}: done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
