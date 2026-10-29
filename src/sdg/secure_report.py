"""``sdg report``: one audited report of real files, for the secure machine.

The report joins three parts: the glimpse of every file, then the design and
the group description of every plain table. The audit of ``sdg audit`` checks
the joined text against the real files, and the report exists only when the
audit passes. One file then crosses to the workstation, after a person reads it.

A description finds its roles from the column names. To set a role by hand,
run ``sdg describe`` on the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sdg import privacy
from sdg.audit import Finding, find_leaks, sensitive_values
from sdg.describe import NoGroupsError, describe_table, sheet_labels
from sdg.formats import detect_module, olink, tabular
from sdg.glimpse import expand_targets, glimpse
from sdg.report import Report, render
from sdg.tables import Table, read_tables
from sdg.version import SDG_VERSION


@dataclass
class SecureReport:
    """The joined report of ``sdg report`` and its audit.

    Attributes:
        text: The report text, empty when the audit fails.
        findings: The real values that the audit found in the joined text.
    """

    text: str
    findings: list[Finding] = field(default_factory=list)


def is_plain_table(path: Path, options: privacy.Options) -> bool:
    """Say whether ``sdg describe`` reads a file.

    Args:
        path: The file.
        options: The report options, for a forced format.

    Returns:
        True for a CSV, TSV, Excel or Parquet table, and for a long Olink
        export that is not Parquet.
    """
    module = detect_module(path, options.format)
    if module is tabular:
        return True
    suffix = privacy.file_suffix(path)
    return (module is olink and suffix in tabular.SUFFIXES and suffix != ".parquet"
            and not olink.is_wide(path))


def plain_tables(files: list[Path], options: privacy.Options) -> tuple[list[tuple[Path, str, Table]], list[str]]:
    """List the plain tables of the files, with the labels of the glimpse.

    Args:
        files: The files, in glimpse order.
        options: The report options.

    Returns:
        Each table with a header, with its file and its label, and a note for
        each file that is not a plain table.
    """
    tables, notes = [], []
    for index, path in enumerate(files, start=1):
        label = privacy.generic_file_label(index, path)
        if not is_plain_table(path, options):
            notes.append(f"{label}: not a plain table, no description")
            continue
        every = [tabular.kept_table(table, options) for table in read_tables(path)]
        shown = sheet_labels(every, options.generic_labels)
        tables += [(path, tabular.table_label(label, shown.get(table.sheet) if table.sheet else None), table)
                   for table in every if table.header]
    return tables, notes


def describe_tables(command: str, tables: list[tuple[Path, str, Table]], options: privacy.Options,
                    notes: list[str]) -> Report:
    """Describe every plain table as a design or as group differences.

    A table with no two groups gets a note in place of a group description.
    The note holds the generic label only, never a column name.

    Args:
        command: ``design`` or ``groups``.
        tables: The plain tables with their files and labels.
        options: The report options.
        notes: Notes to print at the top of the part.

    Returns:
        The part of the report.
    """
    report = Report(comments=[f"sdg describe {command} {SDG_VERSION}", *notes])
    for path, label, table in tables:
        try:
            report.tables.append(describe_table(command, path, label, table, {}, options))
        except NoGroupsError:
            report.comments.append(f"{label}: no two groups to compare")
    return report


def secure_report(targets: list[Path], options: privacy.Options) -> SecureReport:
    """Build the joined report of the targets and audit it.

    Args:
        targets: Real files or directories.
        options: The report options. With a label key, the glimpse fills it.

    Returns:
        The report text, or the audit findings when the audit fails.
    """
    files = expand_targets(targets, options.recursive)
    tables, notes = plain_tables(files, options)
    parts = [glimpse(targets, options), describe_tables("design", tables, options, notes),
             describe_tables("groups", tables, options, [])]
    text = "".join(render(part) for part in parts)
    findings = find_leaks(text, sensitive_values(targets, options))
    if findings:
        return SecureReport("", findings)
    head = render(Report(comments=[f"sdg report {SDG_VERSION}", "audit: passed"]))
    return SecureReport(head + text)
