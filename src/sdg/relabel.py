"""``sdg relabel``: give real data the generic names of a label key.

With ``--generic-labels``, the glimpse and the profile name each column by its
kind, such as ``numeric_1``, each level as ``level_<n>`` and each sheet as
``sheet_<n>``. The synthetic data has these names, so the code written on it
uses them too. On the secure machine, this module gives the real data the same
names from the label key of ``--label-key``: as a copy of each CSV, TSV or Excel
file, or as a renamed pandas DataFrame. The code then runs on the real data with
no change. ``reverse=True`` gives a result of that code the real names again.

The copies hold real data. They stay on the secure machine, and the privacy
rules no longer see a private column such as ``age`` in them, so a glimpse of a
copy is not a safe report: run the glimpse on the real files.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
from openpyxl.cell.cell import MergedCell

from sdg import privacy
from sdg.formats import detect_module, olink, tabular
from sdg.glimpse import expand_targets
from sdg.tables import Table, import_pyreadr, read_text, read_text_table, read_workbook

#: Prefix of the temporary sheet titles while the sheets of a workbook change names.
TEMPORARY_TITLE = "sdg_relabel_"


class NotInKeyError(ValueError):
    """A file is not in the label key."""


@dataclass(frozen=True)
class KeyRow:
    """One row of a label key; see :class:`sdg.privacy.LabelKey`.

    Attributes:
        item: What the name stands for: ``file``, ``sheet``, ``column`` or ``level``.
        file: Generic label of the file, empty for a file row.
        table: Generic sheet name, or ``obs`` or ``var``; empty for a plain table.
        column: Generic column name of a level, else empty.
        generic: The generic name.
        real: The real name.
    """

    item: str
    file: str
    table: str
    column: str
    generic: str
    real: str


@dataclass
class TableNames:
    """The generic names of the columns and the levels of one table.

    Attributes:
        columns: Each real column name mapped to its generic name.
        levels: For each generic column name, each real level mapped to its
            generic level.
    """

    columns: dict[str, str] = field(default_factory=dict)
    levels: dict[str, dict[str, str]] = field(default_factory=dict)


def read_key(path: Path) -> list[KeyRow]:
    """Read a label key.

    Args:
        path: The CSV key that ``--label-key`` wrote.

    Returns:
        The rows of the key.

    Raises:
        ValueError: If the file is not a label key.
    """
    text = path.read_text(encoding="utf-8")
    if not privacy.is_label_key(text):
        raise ValueError(f"{path.name}: not a label key; its first line must be {','.join(privacy.KEY_FIELDS)}")
    records = list(csv.reader(io.StringIO(text.lstrip("﻿"))))[1:]
    return [KeyRow(*record) for record in records if len(record) == len(privacy.KEY_FIELDS)]


def file_label(rows: list[KeyRow], name: str | Path) -> str:
    """Find the generic label of a real file in the key.

    The real path in the key is the path that the glimpse read, so it is
    relative to the folder where the glimpse ran. A path that resolves to the
    same file matches first, then a generic label, then a unique file name.

    Args:
        rows: The rows of the key.
        name: The real path or file name, or the generic label.

    Returns:
        The generic label, such as ``file_2.xlsx``.

    Raises:
        NotInKeyError: If no file of the key has this path, label or name.
        ValueError: If several files of the key have this name.
    """
    files = [row for row in rows if row.item == "file"]
    path = Path(name)
    for row in files:
        if Path(row.real).resolve() == path.resolve():
            return row.generic
    for row in files:
        if row.generic == str(name):
            return row.generic
    named = [row for row in files if Path(row.real).name == path.name]
    if len(named) == 1:
        return named[0].generic
    if not named:
        raise NotInKeyError(f"{path.name}: not in the label key")
    raise ValueError(f"{path.name}: {len(named)} files of the label key have this name; "
                     "give the path that the glimpse read")


def table_field(rows: list[KeyRow], label: str, sheet: str | None) -> str | None:
    """Find the ``table`` field of the key rows of one table.

    Args:
        rows: The rows of the key.
        label: Generic label of the file.
        sheet: The real or the generic sheet name. None takes the one table
            of the file.

    Returns:
        The generic sheet name, ``""`` for a text file, the sheet itself when
        the key names no sheet of the file, or None when the key names other
        sheets of the file only.

    Raises:
        ValueError: If ``sheet`` is None and the key names several sheets.
    """
    sheets = [row for row in rows if row.item == "sheet" and row.file == label]
    if sheet is None:
        if len(sheets) > 1:
            raise ValueError(f"{label}: the label key names {len(sheets)} sheets; choose one")
        return sheets[0].generic if sheets else ""
    for row in sheets:
        if row.real == sheet:
            return row.generic
    for row in sheets:
        if row.generic == sheet:
            return row.generic
    return None if sheets else sheet


def table_names(rows: list[KeyRow], label: str, table: str) -> TableNames:
    """Collect the generic names of one table.

    Args:
        rows: The rows of the key.
        label: Generic label of the file.
        table: The ``table`` field of the table; see :func:`table_field`.

    Returns:
        The generic column and level names.
    """
    names = TableNames()
    for row in rows:
        if row.file != label or row.table != table:
            continue
        if row.item == "column":
            names.columns[row.real] = row.generic
        elif row.item == "level":
            names.levels.setdefault(row.column, {})[row.real] = row.generic
    return names


def header_plan(header: list[str], names: TableNames) -> tuple[list[str | None], dict[int, dict[str, str]]]:
    """Plan the new names of one table.

    Args:
        header: The real column names, in file order.
        names: The generic names of the table.

    Returns:
        The generic name of each column, or None to keep its name, and the
        level names of each categorical column, by column position.
    """
    renamed = [names.columns.get(name) for name in header]
    levels = {}
    for index, name in enumerate(header):
        mapping = names.levels.get(renamed[index] or name)
        if mapping:
            levels[index] = mapping
    return renamed, levels


def relabel_text(path: Path, names: TableNames, target: Path) -> None:
    """Copy a CSV or TSV file with generic column names and levels.

    The header search of the glimpse finds the header, so the rows above the
    header and below the data stay as they are. The copy is UTF-8, as the
    synthetic data is.

    Args:
        path: The real file.
        names: The generic names of its table.
        target: The copy.
    """
    table = read_text_table(path)
    records = list(csv.reader(read_text(path).splitlines(), delimiter=table.delimiter))
    top = len(table.preamble)
    renamed, levels = header_plan(table.header, names)
    for index, generic in enumerate(renamed):
        if generic is not None:
            records[top][index] = generic
    for record in records[top + 1:top + 1 + len(table.rows)]:
        for index, mapping in levels.items():
            if index < len(record) and record[index] in mapping:
                record[index] = mapping[record[index]]
    newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    with target.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, delimiter=table.delimiter or ",", lineterminator=newline).writerows(records)


def relabel_parquet(path: Path, names: TableNames, target: Path) -> None:
    """Copy a Parquet file with generic column names and levels.

    Each column keeps its type. A column with generic levels is a text column,
    because only a text column has categorical levels.

    Args:
        path: The real file.
        names: The generic names of its table.
        target: The copy.
    """
    import pyarrow as pa
    import pyarrow.parquet as parquet

    data = parquet.read_table(path)
    renamed, levels = header_plan([str(name) for name in data.column_names], names)
    arrays = []
    for index, column in enumerate(data.columns):
        mapping = levels.get(index)
        if mapping:
            column = pa.array([None if value is None else mapping.get(str(value), value)
                               for value in column.to_pylist()], pa.string())
        arrays.append(column)
    header = [generic or real for generic, real in zip(renamed, data.column_names, strict=True)]
    parquet.write_table(pa.Table.from_arrays(arrays, names=header), target)


def relabel_rds(path: Path, names: TableNames, target: Path) -> None:
    """Copy an RDS file of one data frame with generic column names and levels.

    Args:
        path: The real file.
        names: The generic names of its table.
        target: The copy.
    """
    pyreadr = import_pyreadr()
    frame = next(iter(pyreadr.read_r(str(path)).values()))
    renamed, levels = header_plan([str(name) for name in frame.columns], names)
    for index, mapping in levels.items():
        frame.iloc[:, index] = [mapping.get(str(value), value) if pd.notna(value) else value
                                for value in frame.iloc[:, index]]
    frame.columns = [generic or real for generic, real in zip(renamed, frame.columns, strict=True)]
    pyreadr.write_rds(str(target), frame)


def writable_cell(sheet: Any, row: int, column: int) -> Any:
    """Return a cell that takes a value, out of its merged range if it is in one.

    A header cell with no name can sit inside a merged range, such as a title
    that spans three rows. The glimpse gives that column a generic name, so the
    copy must write it; openpyxl writes no merged cell but the first, so the
    range is unmerged.

    Args:
        sheet: The worksheet of a workbook opened for writing.
        row: Row number, from 1.
        column: Column number, from 1.

    Returns:
        The cell.
    """
    cell = sheet.cell(row=row, column=column)
    if isinstance(cell, MergedCell):
        merged = next(item for item in sheet.merged_cells.ranges if cell.coordinate in item)
        sheet.unmerge_cells(str(merged))
        cell = sheet.cell(row=row, column=column)
    return cell


def relabel_sheet(sheet: Any, table: Table, names: TableNames) -> None:
    """Give one worksheet generic column names and levels.

    The reader of the glimpse reads each sheet from cell ``A1``, so the
    position of a cell in the table gives its row and its column.

    Args:
        sheet: The worksheet of a workbook opened for writing.
        table: The same sheet as the glimpse reads it.
        names: The generic names of the table.
    """
    if not table.header:
        return
    top = len(table.preamble) + 1
    renamed, levels = header_plan(table.header, names)
    for index, generic in enumerate(renamed):
        if generic is not None:
            writable_cell(sheet, top, index + 1).value = generic
    for offset in range(1, len(table.rows) + 1):
        for index, mapping in levels.items():
            cell = sheet.cell(row=top + offset, column=index + 1)
            if cell.value is not None and str(cell.value) in mapping:
                cell.value = mapping[str(cell.value)]


def rename_sheets(workbook: Any, titles: dict[str, str]) -> None:
    """Give sheets their generic names, through temporary titles.

    A generic name can be the real name of another sheet, such as a real sheet
    ``sheet_2`` that becomes ``sheet_1``. A direct rename would then clash.

    Args:
        workbook: The workbook opened for writing.
        titles: Each real sheet title mapped to its generic name.
    """
    sheets = [(workbook[real], generic) for real, generic in titles.items()]
    for index, (sheet, _) in enumerate(sheets):
        sheet.title = f"{TEMPORARY_TITLE}{index}"
    for sheet, generic in sheets:
        sheet.title = generic


def relabel_workbook(path: Path, rows: list[KeyRow], label: str, target: Path) -> None:
    """Copy an Excel workbook with generic sheet names, column names and levels.

    A formula cell keeps its stored value and loses its formula, because the
    copy is for analysis code, which reads values. A sheet that the key does
    not name stays as it is.

    Args:
        path: The real workbook.
        rows: The rows of the key.
        label: Generic label of the workbook.
        target: The copy.
    """
    tables = {table.sheet: table for table in read_workbook(path)}
    workbook = openpyxl.load_workbook(path, data_only=True)
    try:
        titles = {}
        for sheet in workbook.worksheets:
            table = table_field(rows, label, sheet.title)
            if table is None:
                continue
            relabel_sheet(sheet, tables[sheet.title], table_names(rows, label, table))
            if table != sheet.title:
                titles[sheet.title] = table
        rename_sheets(workbook, titles)
        workbook.save(target)
    finally:
        workbook.close()


def relabel_file(path: Path, rows: list[KeyRow], out_dir: Path) -> Path:
    """Copy one real file with the generic names of the key.

    Args:
        path: The real file.
        rows: The rows of the key.
        out_dir: The folder of the copies.

    Returns:
        The copy, named by the generic label of the file, as the synthetic
        file is.

    Raises:
        NotInKeyError: If the file is not in the key.
        ValueError: If the file is not a CSV, TSV or Excel table, or if the
            copy exists already.
    """
    label = file_label(rows, path)
    module = detect_module(path)
    suffix = privacy.file_suffix(path)
    if module not in (tabular, olink) or suffix not in tabular.SUFFIXES or (module is olink and olink.is_wide(path)):
        raise ValueError(f"{path.name}: sdg relabel copies CSV, TSV, Excel, Parquet and RDS tables only")
    target = out_dir / label
    if target.exists():
        raise ValueError(f"{target} exists; choose a new output folder")
    if suffix in tabular.WORKBOOK_SUFFIXES:
        relabel_workbook(path, rows, label, target)
    elif suffix == ".parquet":
        relabel_parquet(path, table_names(rows, label, ""), target)
    elif suffix == ".rds":
        relabel_rds(path, table_names(rows, label, ""), target)
    else:
        relabel_text(path, table_names(rows, label, ""), target)
    return target


def relabel_files(targets: list[Path], key: Path, out_dir: Path,
                  recursive: bool = False) -> tuple[list[tuple[Path, Path]], list[str]]:
    """Copy real files with the generic names of a label key.

    Args:
        targets: Real files or directories.
        key: The label key.
        out_dir: The folder of the copies. It is made when it does not exist.
        recursive: Also read the subdirectories of a directory.

    Returns:
        Each real file with its copy, and a note for each file that the key
        does not hold.

    Raises:
        ValueError: If the key cannot be read, or a file cannot be copied.
    """
    rows = read_key(key)
    files = [path for path in expand_targets(targets, recursive) if path.resolve() != key.resolve()]
    out_dir.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []
    for path in files:
        try:
            written.append((path, relabel_file(path, rows, out_dir)))
        except NotInKeyError as error:
            skipped.append(str(error))
    return written, skipped


def invert(mapping: dict[str, str]) -> dict[str, str]:
    """Swap the keys and the values of a name map.

    Args:
        mapping: Each real name mapped to its generic name.

    Returns:
        Each generic name mapped to its real name.
    """
    return {generic: real for real, generic in mapping.items()}


def rename_levels(series: pd.Series, mapping: dict[str, str]) -> pd.Series:
    """Rename the levels of one column; other values and missing values stay.

    Args:
        series: The column.
        mapping: Each old level, as text, mapped to its new level.

    Returns:
        The column with the new levels. A text column keeps its type; another
        column, such as one of numeric codes, holds objects.
    """
    values = series.astype(object)
    text = values.map(str, na_action="ignore")
    renamed = values.where(~text.isin(list(mapping)), text.map(mapping))
    if pd.api.types.is_string_dtype(series.dtype) and not isinstance(series.dtype, pd.CategoricalDtype):
        return renamed.astype(series.dtype)
    return renamed


def rename_frame_levels(frame: pd.DataFrame, levels: dict[str, dict[str, str]]) -> pd.DataFrame:
    """Rename the levels of the columns of a DataFrame.

    Args:
        frame: The data.
        levels: For each column name, each old level mapped to its new level.

    Returns:
        A copy with the new levels.
    """
    out = frame.copy()
    for position, name in enumerate(out.columns):
        mapping = levels.get(str(name))
        if mapping:
            out.isetitem(position, rename_levels(out.iloc[:, position], mapping))
    return out


def relabel_frame(frame: pd.DataFrame, key: str | Path, file: str | Path, sheet: str | None = None,
                  reverse: bool = False) -> pd.DataFrame:
    """Give a DataFrame of real data the generic names of a label key.

    Read the real file into a DataFrame, rename it, then run the code written
    on the synthetic data. With ``reverse=True``, a result of that code with
    generic column names and levels gets the real names again.

    Args:
        frame: The real data, or a result with generic names.
        key: The label key of the glimpse.
        file: The real path or file name of the data, or its generic label,
            such as ``file_1.xlsx``.
        sheet: The real or the generic sheet name of a workbook. None takes
            the one sheet or the text file.
        reverse: Give generic names their real names again.

    Returns:
        A renamed copy of the DataFrame. A column that the key does not name
        keeps its name.

    Raises:
        ValueError: If the key does not hold the file or the sheet.
    """
    rows = read_key(Path(key))
    label = file_label(rows, file)
    table = table_field(rows, label, sheet)
    if table is None:
        raise ValueError(f"{label}: no sheet {sheet!r} in the label key")
    names = table_names(rows, label, table)
    if reverse:
        levels = {column: invert(mapping) for column, mapping in names.levels.items()}
        return rename_frame_levels(frame, levels).rename(columns=invert(names.columns))
    return rename_frame_levels(frame.rename(columns=names.columns), names.levels)
