"""Privacy rules shared by ``sdg glimpse``, ``sdg profile`` and ``sdg audit``.

These are the privacy rules:

1. An identifier column records the shape of its values only.
2. In a table with a private column, the private columns record counts
   only. The other columns record safe facts only: identifier shapes, the
   common shapes of a text column, decimals, the codes of a column of few
   numbers, and the quantiles from 5% to 95% of a column of 50 numbers or more.
3. Column names and category labels stay unless ``--generic-labels`` is set.
   A generic column name holds the kind of the column, such as ``numeric_2``.
4. A column with more than ``MAX_LEVELS`` distinct text values records shapes
   and lengths only.
5. Numbers are rounded to ``SIGNIFICANT_DIGITS``; a column with fewer than
   ``MIN_VALUES`` numbers records its kind and missing share only.
6. Free text records its length range, and its common shapes when they fill
   half of its cells.
7. Dates record intervals, never an absolute date.
8. File names and instrument metadata become generic tokens.
9. No report or config holds a sequence.
10. A pair of numeric columns with 50 rows of two numbers records its rank
    correlation, rounded to 2 decimals. A private column records none.
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: Column names that hold identifiers. Their values print as shapes.
ID_PATTERN = re.compile(r"subject|donor|patient|sample|plate(?!let)|barcode|\bid\b|_id$|^id", re.IGNORECASE)

#: A name that matches :data:`ID_PATTERN` but ends with one of these words
#: describes the identified item, as ``Sample_Type`` or ``SampleQC`` of an Olink
#: export do. Its values are levels such as ``CONTROL``, which analysis code
#: filters on, so the column is not an identifier.
ID_QUALIFIER = re.compile(r"(type|qc|status|warning|group|class)\s*$", re.IGNORECASE)

#: A table with any of these columns holds person-level fields, so it records
#: counts only, whatever its file name.
PRIVATE_COLUMNS = re.compile(r"\bage\b|\bsex\b|gender|\bbmi\b|weight|birth|comorbid|medication|"
                             r"therapy|visit date|\becog\b|karnofsky|\bnyha\b|das28|"
                             r"\bmeld\b|apache", re.IGNORECASE)

#: Default pattern of file names whose tables record counts only.
DEFAULT_PRIVATE_FILES = "clinical"

#: The marker of a report that holds real values by design, such as the
#: Quarto report of ``sdg quarto``. ``sdg audit`` refuses a text with it.
SECURE_MARKER = "sdg-secure-only: this report holds real values"

#: A column is numeric when at least this share of its filled cells parse as
#: numbers and its other cells hold few distinct tokens, such as ``NA``. A share
#: of 0.8 alone lets a column with 26 ``NA`` rows in 120 print every value.
#: The share leaves out the standard missing tokens of
#: :data:`MISSING_TOKEN`, because a survival column can be mostly ``#N/A``.
NUMERIC_SHARE = 0.5
NUMERIC_TOKENS = 5

#: A numeric column with fewer numbers than this records counts only.
MIN_VALUES = 10

#: In a private table, a numeric column that is not a private column and has
#: this many distinct numbers or less keeps its codes, such as 0 and 1. A code
#: needs ``MIN_VALUES`` cells, so a rare code, which can point to one person,
#: is left out.
CODE_LIMIT = 10

#: In a private table, a numeric column that is not a private column and has
#: this many numbers keeps its quantiles from ``TRIM_LOW`` to ``TRIM_HIGH``. The
#: minimum and the maximum, each the value of one person, never print.
TRIM_MIN_VALUES = 50
TRIM_LOW = 0.05
TRIM_HIGH = 0.95

#: A text column with more distinct values than this records shapes only.
MAX_LEVELS = 100

#: A text column is categorical only when its values repeat: at most this share
#: of its filled cells may be distinct. A column of mostly unique values is free
#: text or an identifier, and its values never print.
CATEGORY_DISTINCT_SHARE = 0.5

#: Numbers in a report or a config keep this many significant digits.
SIGNIFICANT_DIGITS = 3

#: Standard missing-value tokens, such as ``NA``, ``?`` or ``<LOD``. They hold
#: no personal value, so a private table keeps them. UCI and MIMIC files use ``?``.
MISSING_TOKEN = re.compile(r"^(na|n/a|nan|none|null|nd|nr|nq|#n/a|\.|-+|\?+|[<>]\s*\S*)$", re.IGNORECASE)

#: A pair of numeric columns records its rank correlation (rule 10) when it
#: has ``TRIM_MIN_VALUES`` rows with two numbers and at least this absolute
#: correlation. A table records at most ``MAX_CORRELATIONS`` pairs, the
#: strongest, and only when it has at most ``CORRELATION_COLUMNS`` numeric columns.
MIN_CORRELATION = 0.2
MAX_CORRELATIONS = 20
CORRELATION_COLUMNS = 40

#: Decimal places of a recorded correlation.
CORRELATION_DECIMALS = 2

#: A digit code with a leading zero, such as the ZIP code ``01013``.
LEADING_ZERO = re.compile(r"0+\d+")

#: A numeric column is a code column, and so an identifier, when at least this
#: share of its numbers are written with a leading zero.
CODE_SHARE = 0.1

#: A run of this many nucleotide letters counts as a sequence (rule 9).
SEQUENCE = re.compile(r"[ACGTNacgtn]{20,}")

#: A name that ``--generic-labels`` prints. A real name of this form, such as a
#: sheet already called ``sheet_1``, tells nothing, so the audit skips it.
GENERIC_NAME = re.compile(r"(?:file|sheet|column|level|identifier|numeric|categorical|text|date|boolean)_\d+")

#: Compound suffixes kept whole in a generic file label.
COMPRESSION_SUFFIXES = {".gz", ".bz2", ".xz", ".zst"}

#: Header of a label key file. The audit refuses a checked text that starts
#: with it, because the key holds the real names.
KEY_FIELDS = ("item", "file", "table", "column", "generic", "real")


@dataclass
class LabelKey:
    """The real name behind each generic name of a glimpse or a profile.

    The key stays on the secure machine. With it, a person renames the real
    columns, sheets and levels to the generic names, so code written on the
    synthetic data runs on the real data.

    Attributes:
        rows: One row per generic name, in the order of :data:`KEY_FIELDS`.
    """

    rows: dict[tuple[str, ...], None] = field(default_factory=dict)

    def add(self, item: str, file: str, table: str, column: str, generic: str, real: object) -> None:
        """Record one generic name; a repeated row is kept once.

        Args:
            item: What the name stands for: ``file``, ``sheet``, ``column`` or ``level``.
            file: Generic label of the file, empty for a file row.
            table: Generic sheet name, or ``obs`` or ``var``; empty for a plain table.
            column: Column name of a level as the output writes it, else empty.
            generic: The generic name.
            real: The real name.
        """
        self.rows[(item, file, table, column, generic, str(real))] = None

    def write(self, path: Path) -> None:
        """Write the key as a CSV file.

        Args:
            path: The output file.
        """
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(KEY_FIELDS)
            writer.writerows(self.rows)


@dataclass(frozen=True)
class Options:
    """Options of ``sdg glimpse`` and ``sdg profile``.

    Attributes:
        private_files: File names that match this pattern record counts only.
        generic_labels: Replace column names, sheet names and category labels
            with generic names.
        max_categories: Most categories, shapes or tokens listed per column.
        format: Format name that overrides the detection by file suffix.
        recursive: Read the subdirectories of a directory target.
        extra: Format-specific settings, such as the FASTQ reference.
        key: Collects the real name behind each generic name, for
            ``--label-key``. ``None`` collects nothing.
        drop: Columns of a plain table whose names match this pattern are
            left out of the report and the config, as if the table did not
            have them. The audit still searches for their values.
    """

    private_files: re.Pattern[str] = re.compile(DEFAULT_PRIVATE_FILES, re.IGNORECASE)
    generic_labels: bool = False
    max_categories: int = MAX_LEVELS
    format: str | None = None
    recursive: bool = False
    extra: dict[str, str] = field(default_factory=dict)
    key: LabelKey | None = None
    drop: re.Pattern[str] | None = None


def shape(value: object) -> str:
    """Replace each letter with ``A`` and each digit with ``9``.

    Args:
        value: A cell as read.

    Returns:
        The shape of the cell, which keeps its separators and its length.
    """
    return re.sub(r"[0-9]", "9", re.sub(r"[A-Za-z]", "A", str(value)))


def code_shape(value: object) -> str:
    """Return the shape of an identifier, with the leading zeros of a digit code kept.

    A ZIP code such as ``01013`` has the shape ``09999``, so its synthetic copy
    keeps the zero that code which reads it as text expects.

    Args:
        value: A cell as read.

    Returns:
        The shape.
    """
    text = str(value)
    if LEADING_ZERO.fullmatch(text):
        zeros = len(text) - len(text.lstrip("0"))
        return "0" * zeros + "9" * (len(text) - zeros)
    return shape(text)


def round_sig(value: float, digits: int = SIGNIFICANT_DIGITS) -> float:
    """Round a number to a count of significant digits.

    Args:
        value: The number.
        digits: Significant digits to keep.

    Returns:
        The rounded number. Zero and non-finite numbers return unchanged.
    """
    if value == 0 or not math.isfinite(value):
        return value
    return float(f"{value:.{digits}g}")


def format_number(value: float) -> str:
    """Print a number rounded to the report precision.

    Args:
        value: The number.

    Returns:
        The rounded number as short text, such as ``0.123`` or ``1.23e+06``.
    """
    rounded = round_sig(float(value))
    return str(int(rounded)) if rounded.is_integer() and abs(rounded) < 1e15 else f"{rounded:g}"


def is_identifier_name(name: object) -> bool:
    """Say whether a column name marks an identifier column.

    Args:
        name: Column name.

    Returns:
        True when the name matches :data:`ID_PATTERN` and does not end with a
        word of :data:`ID_QUALIFIER`.
    """
    return bool(ID_PATTERN.search(str(name))) and not ID_QUALIFIER.search(str(name))


def is_private_name(name: object) -> bool:
    """Say whether a column name marks a person-level field.

    Args:
        name: Column name.

    Returns:
        True when the name matches :data:`PRIVATE_COLUMNS`.
    """
    return bool(PRIVATE_COLUMNS.search(str(name)))


def file_suffix(path: Path) -> str:
    """Return the suffix of a file, with a compression suffix kept whole.

    Args:
        path: A file path.

    Returns:
        ``.fastq.gz`` for ``reads.fastq.gz``, ``.csv`` for ``table.csv``.
    """
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if len(suffixes) >= 2 and suffixes[-1] in COMPRESSION_SUFFIXES:
        return "".join(suffixes[-2:])
    return suffixes[-1] if suffixes else ""


def generic_file_label(index: int, path: Path) -> str:
    """Return the generic label of the n-th file of a report (rule 8).

    Args:
        index: Position of the file, from 1.
        path: The real path. Only its suffix survives.

    Returns:
        A label such as ``file_2.xlsx``.
    """
    return f"file_{index}{file_suffix(path)}"


def generic_names(names: list[str], prefix: str) -> dict[str, str]:
    """Map each distinct name to a generic name, in order of first use.

    Args:
        names: Names, possibly repeated.
        prefix: Prefix of the generic names, such as ``column``.

    Returns:
        Each distinct name mapped to ``<prefix>_<n>``.
    """
    mapping: dict[str, str] = {}
    for name in names:
        mapping.setdefault(name, f"{prefix}_{len(mapping) + 1}")
    return mapping


def kind_names(columns: list[tuple[object, str]]) -> dict[object, str]:
    """Map each distinct column name to a generic name that holds its kind.

    The numbers count per kind, in column order: ``CD8`` and ``CD4`` become
    ``numeric_1`` and ``numeric_2``, and ``disease_status`` becomes
    ``categorical_1``. A repeated name keeps one generic name, from the kind
    of its first column, so the report still shows the repeat.

    Args:
        columns: The name and the kind of each column, in column order.

    Returns:
        Each distinct name mapped to ``<kind>_<n>``.
    """
    mapping: dict[object, str] = {}
    counts: Counter[str] = Counter()
    for name, kind in columns:
        if name not in mapping:
            counts[kind] += 1
            mapping[name] = f"{kind}_{counts[kind]}"
    return mapping


def note_key(options: Options, item: str, file: str, table: str | None, column: object,
             names: dict) -> None:
    """Add generic names to the label key of the options, if it collects.

    Args:
        options: The glimpse or profile options.
        item: What the names stand for, such as ``column`` or ``level``.
        file: Generic label of the file.
        table: Generic sheet name, or ``obs`` or ``var``; ``None`` for a plain table.
        column: Column name of the levels as the output writes it, or ``""``.
        names: Each real name mapped to its generic name.
    """
    if options.key is None:
        return
    for real, generic in names.items():
        options.key.add(item, file, table or "", str(column), generic, real)


def is_generic_name(value: str) -> bool:
    """Say whether a value has the form of a generic name.

    Args:
        value: A real name, such as a sheet name.

    Returns:
        True for a name such as ``sheet_1`` or ``numeric_12``.
    """
    return bool(GENERIC_NAME.fullmatch(value.strip()))


def is_label_key(text: str) -> bool:
    """Say whether a text is a label key file.

    Args:
        text: A checked text.

    Returns:
        True when the first line is the header of a label key.
    """
    return text.lstrip("﻿").split("\n", 1)[0].strip() == ",".join(KEY_FIELDS)


def holds_sequence(text: str) -> bool:
    """Say whether a text holds a nucleotide sequence (rule 9).

    Args:
        text: A report or a config.

    Returns:
        True when the text holds a run of at least 20 nucleotide letters.
    """
    return bool(SEQUENCE.search(text))


def is_missing_token(token: str) -> bool:
    """Say whether a non-numeric cell is a standard missing-value token.

    Args:
        token: The cell text.

    Returns:
        True for a token such as ``NA``, ``N/A``, ``-``, ``<LOD`` or ``>ULOQ``.
    """
    return bool(MISSING_TOKEN.match(token.strip()))


def is_missing_level(level: str) -> bool:
    """Say whether a category level is a missing token that keeps its text.

    A generic or private category column keeps such a level, because code
    reads ``NA`` or ``?`` as a missing cell. A limit token such as ``<LOD``
    does not count: in a category column it can be a band such as ``<18``.

    Args:
        level: The level text.

    Returns:
        True for a level such as ``NA``, ``N/A``, ``-`` or ``?``.
    """
    return is_missing_token(level) and not level.strip().startswith(("<", ">"))
