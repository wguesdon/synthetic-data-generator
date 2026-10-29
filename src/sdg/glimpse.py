"""``sdg glimpse``: a structure report of real files with no real value.

The report follows the style of ``dplyr::glimpse``. It gives each file a generic label, so no
real file name leaves the secure machine, and it follows the privacy rules in
:mod:`sdg.privacy`.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sdg import privacy
from sdg.formats import KNOWN_SUFFIXES, detect_module
from sdg.report import Report, render
from sdg.version import SDG_VERSION


def expand_targets(targets: list[Path], recursive: bool) -> list[Path]:
    """List the files under the targets, in name order.

    Args:
        targets: Files or directories.
        recursive: Also read the subdirectories of a directory.

    Returns:
        The files. A directory contributes only files with a known suffix, and
        never an Office lock file (``~$``) or a hidden file.

    Raises:
        FileNotFoundError: If a target does not exist.
    """
    found: list[Path] = []
    for target in targets:
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            candidates = target.rglob("*") if recursive else target.glob("*")
            found.extend(sorted(path for path in candidates
                                if path.is_file() and privacy.file_suffix(path) in KNOWN_SUFFIXES
                                and not path.name.startswith(("~$", "."))))
        else:
            raise FileNotFoundError(f"target not found: {target}")
    return list(dict.fromkeys(found))


def glimpse(targets: list[Path], options: privacy.Options) -> Report:
    """Build the glimpse report of every file under the targets.

    Files go to their format module in groups, so a module can report a folder
    of RCC or FCS files as one dataset.

    Args:
        targets: Files or directories.
        options: The glimpse options.

    Returns:
        The report.
    """
    files = expand_targets(targets, options.recursive)
    groups: dict[str, tuple] = {}
    for index, path in enumerate(files, start=1):
        module = detect_module(path, options.format)
        paths, labels = groups.setdefault(module.__name__, (module, [], []))[1:]
        paths.append(path)
        labels.append(privacy.generic_file_label(index, path))
        privacy.note_key(options, "file", "", None, "", {path: labels[-1]})
    report = Report(comments=[f"sdg glimpse {SDG_VERSION}", f"files: {len(files)}"])
    for module, paths, labels in groups.values():
        report.tables.extend(module.glimpse(paths, labels, options))
    return report


def timestamp() -> str:
    """Return the current time in the file name form of the repository.

    Returns:
        A stamp such as ``2026_10_11_142530``.
    """
    return dt.datetime.now().strftime("%Y_%m_%d_%H%M%S")


def report_path(prefix: Path, stem: str = "glimpse") -> Path:
    """Choose the file of a report written with ``-o``.

    Args:
        prefix: An existing directory, or a path prefix.
        stem: Start of the file name in a directory, such as ``design``.

    Returns:
        ``<dir>/<stem>_<stamp>.txt`` for a directory, else ``<prefix>_<stamp>.txt``.
    """
    if prefix.is_dir():
        return prefix / f"{stem}_{timestamp()}.txt"
    return prefix.with_name(f"{prefix.name.removesuffix('.txt')}_{timestamp()}.txt")


def write_report(report: Report, prefix: Path, stem: str = "glimpse") -> Path:
    """Write a report to a timestamped file.

    Args:
        report: The report.
        prefix: An existing directory, or a path prefix.
        stem: Start of the file name in a directory, such as ``design``.

    Returns:
        The file written.
    """
    path = report_path(prefix, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(report), encoding="utf-8")
    return path
