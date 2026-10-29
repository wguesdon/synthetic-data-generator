"""``sdg audit``: check a config or a glimpse report for real values.

The audit runs on the secure machine, next to the real data. It collects every
value that the privacy rules keep out, such as identifiers, free text, dates and
file names, and searches the checked text for each. It also fails on any
nucleotide sequence (rule 9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from operator import attrgetter
from pathlib import Path

from sdg import privacy
from sdg.config import UNION_TAGS, Config
from sdg.formats import detect_module
from sdg.glimpse import expand_targets
from sdg.report import TABLE_LINE

#: A value shorter than this is too common to search for.
MIN_LENGTH = 3

#: A value of exactly ``MIN_LENGTH`` characters must match a whole word.
SHORT_LENGTH = 3

#: Value kinds that always match as a whole word. A file stem such as ``plate``
#: is a common word, so it must not match inside ``plate_export``.
WORD_KINDS = {"file name"}

#: Kinds of the real names that ``--generic-labels`` replaces. A report prints
#: each name quoted, such as ``$ 'CD8' <numeric>`` or ``values: 'patient' 12``, so
#: in a report a name matches only as a quoted item. Its own words, such as
#: ``value column`` or ``timepoint``, then never match a column called ``value``
#: or ``time``, as they did on the public MIMIC and heart failure tables. In a
#: config, a name matches as a whole word, unless it is a word of the schema.
NAME_KINDS = {"column name", "sheet name", "label", "marker label"}

#: A value made of digits only, such as the identifier ``85387`` or the cell
#: ``3.0``. A report prints counts, rounded numbers and its version, so such a
#: value matches only as a quoted item: a level, a token or a name. A YAML
#: config quotes such a text too, because it would read as a number. On public
#: MIMIC and UCI files, a bare search found row counts and ``0.3.0``.
NUMBER = re.compile(r"[-+]?\d[\d.,]*")

#: A letter. A value with digits and no letter, such as the MIMIC lab value
#: ``6-10``, matches as a whole token only, so it does not match inside the date
#: ``2026-10-12`` of a profile header.
LETTER = re.compile(r"[A-Za-z]")


def leak_pattern(needle: str, kind: str, report: bool = False) -> str | None:
    """Choose how a sensitive value matches in a checked text.

    A date always matches anywhere, because rule 7 keeps every absolute date out.

    Args:
        needle: The value, stripped.
        kind: Its source kind, such as ``identifier`` or ``date``.
        report: The checked text is a report, not a config.

    Returns:
        A regular expression, or None to match the value anywhere.
    """
    escaped = re.escape(needle)
    if report and kind in NAME_KINDS:
        return rf"{re.escape(repr(needle))}|(['\"]){escaped}\1"
    if kind != "date" and NUMBER.fullmatch(needle):
        return rf"(['\"]){escaped}\1"
    if kind != "date" and not LETTER.search(needle):
        return rf"(?<![\w./-]){escaped}(?![\w./-])"
    if len(needle) == SHORT_LENGTH or kind in WORD_KINDS or kind in NAME_KINDS:
        return rf"(?<![\w-]){escaped}(?![\w-])"
    return None


def schema_words(schema: object) -> set[str]:
    """Collect the property names and enum values of a JSON schema.

    Args:
        schema: A JSON schema, or any part of one.

    Returns:
        Every property name and every string constant, in lower case.
    """
    words: set[str] = set()
    if isinstance(schema, dict):
        words |= {str(key).lower() for key in schema.get("properties", {})}
        words |= {str(value).lower() for value in schema.get("enum", []) if isinstance(value, str)}
        if isinstance(schema.get("const"), str):
            words.add(schema["const"].lower())
        for value in schema.values():
            words |= schema_words(value)
    elif isinstance(schema, list):
        for item in schema:
            words |= schema_words(item)
    return words


#: Words of the config schema, such as ``counts`` or ``cells``. A real file with
#: such a stem is not a leak when the config holds the word.
RESERVED_WORDS = frozenset(schema_words(Config.model_json_schema()) | UNION_TAGS)


@dataclass(frozen=True)
class Finding:
    """One real value found in a checked text.

    Attributes:
        line: Line number in the checked text, from 1.
        kind: Source kind, such as ``identifier`` or ``sequence``.
        value: The real value.
    """

    line: int
    kind: str
    value: str


def sensitive_values(targets: list[Path], options: privacy.Options) -> dict[str, str]:
    """Collect the sensitive values of every real file under the targets.

    Args:
        targets: Real files or directories.
        options: The options that made the checked text.

    Returns:
        Each sensitive value mapped to its source kind. A value with the form
        of a generic name, such as ``sheet_1``, is left out: the generic report
        prints that name for its own reason.
    """
    files = expand_targets(targets, options.recursive)
    groups: dict[str, tuple] = {}
    found: dict[str, str] = {}
    for path in files:
        module = detect_module(path, options.format)
        groups.setdefault(module.__name__, (module, []))[1].append(path)
        stem = path.stem.split(".")[0]
        if stem.lower() not in RESERVED_WORDS:
            found[stem] = "file name"
    for module, paths in groups.values():
        found.update(module.audit_values(paths, options))
    return {value: kind for value, kind in found.items() if not privacy.is_generic_name(value)}


def line_of(text: str, index: int) -> int:
    """Return the line number of a position in a text.

    Args:
        text: The text.
        index: Character position.

    Returns:
        The line number, from 1.
    """
    return text.count("\n", 0, index) + 1


def is_report(text: str) -> bool:
    """Say whether a checked text is a report, not a config.

    Args:
        text: The checked text.

    Returns:
        True when a line opens a table, such as ``== file_1.csv <csv>``.
    """
    return any(TABLE_LINE.match(line) for line in text.splitlines())


def find_leaks(text: str, values: dict[str, str]) -> list[Finding]:
    """Search a text for sensitive values and for sequences.

    Args:
        text: The checked config or report.
        values: Each sensitive value mapped to its source kind.

    Returns:
        One finding per value found, at its first position, and one per
        sequence, in line order. A label key is a finding of its own, because
        it holds the real name behind each generic name.
    """
    findings = [Finding(1, "label key", ",".join(privacy.KEY_FIELDS))] if privacy.is_label_key(text) else []
    report = is_report(text)
    for value, kind in values.items():
        needle = value.strip()
        if len(needle) < MIN_LENGTH:
            continue
        if kind in NAME_KINDS and not report and needle.lower() in RESERVED_WORDS:
            # A config key such as ``value`` or ``group`` is a word of the schema.
            continue
        if privacy.shape(needle) == needle:
            # A value such as "999" or the MIMIC redaction mark "___" is its own
            # shape, which rules 1 and 6 print, so it carries nothing more.
            continue
        pattern = leak_pattern(needle, kind, report)
        if pattern is None:
            index = text.find(needle)
        else:
            match = re.search(pattern, text)
            index = match.start() if match else -1
        if index >= 0:
            findings.append(Finding(line_of(text, index), kind, needle))
    findings += [Finding(line_of(text, match.start()), "sequence", match.group())
                 for match in privacy.SEQUENCE.finditer(text)]
    return sorted(findings, key=attrgetter("line", "kind"))
