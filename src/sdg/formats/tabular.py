"""CSV, TSV, Excel and Parquet datasets.

This module holds the config model of a tabular dataset and the format
functions that every format module supplies: ``glimpse``, ``profile``,
``generate`` and ``audit_values``.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import numpy as np
from pydantic import Field, field_validator, model_validator
from scipy import special
from scipy import stats as scipy_stats

from sdg import fake_values, privacy
from sdg.config_base import DatasetBase, NoteRow, Share, StrictModel, check_non_decreasing
from sdg.report import Fact, TableReport
from sdg.design import DesignRow
from sdg.modes.model import Feature, measured_rows, simulate
from sdg.modes.resample import (UUID_PATTERN, invented_identifiers, invented_text, invented_uuids, pattern_counts,
                                pick_shares, quantile_draws, shaped_text)
from sdg.seeding import stream
from sdg.tables import (BOOLEAN_TEXT, ColumnStats, Table, analyze_column, column_report, common_shapes,
                        generic_columns, is_empty, is_label_row, kept_codes, layout_facts, level_names, note_cells,
                        note_columns, note_fields, note_values, number, parse_date, read_tables, rounded_quantiles,
                        without_suffix,
                        text_provider, trimmed_quantiles, write_parquet, write_rds, write_text_table,
                        write_workbook)

if TYPE_CHECKING:
    from sdg.generate import Context

#: Format names that this module handles.
FORMATS = ("csv", "tsv", "excel", "parquet", "rds")

#: File suffixes that this module reads. The Olink module sniffs a Parquet file first.
SUFFIXES = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".parquet", ".rds")

#: Suffixes of a workbook.
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm", ".xls")

#: Suffixes of a file whose cells keep their types, such as int, float and date.
TYPED_SUFFIXES = (*WORKBOOK_SUFFIXES, ".parquet", ".rds")

#: Formats of one typed table with no rows around it.
TABLE_FORMATS = ("parquet", "rds")

#: Text formats of a Parquet date column: a date, or a date and a time.
PARQUET_DATE = "%Y-%m-%d"
PARQUET_DATETIME = "%Y-%m-%d %H:%M:%S"

#: Largest gap allowed between 1 and the sum of the level shares of a column.
#: Shares written by ``sdg profile`` are rounded, so they rarely sum to exactly 1.
SHARE_TOLERANCE = 0.01


class ColumnBase(StrictModel):
    """Fields that every column carries, whatever its kind.

    Attributes:
        name: Column header exactly as delivered, stray spaces included.
        missing: Share of the rows, empty rows excluded, with an empty cell.
        na_token: Text written in an empty cell, such as ``"NA"``. ``None``
            leaves the cell empty.
    """

    name: str
    missing: Share = 0.0
    na_token: str | None = None


class Link(StrictModel):
    """The column of unique keys that an identifier column refers to.

    Attributes:
        dataset: Name of the dataset of the keys.
        column: Name of the identifier column of the keys.
        sheet: Sheet of a workbook. None uses the first sheet.
    """

    dataset: str = Field(min_length=1)
    column: str = Field(min_length=1)
    sheet: str | None = None


class IdentifierColumn(ColumnBase):
    """A column of identifiers, recorded as a value shape only.

    Attributes:
        pattern: Shape of one value. ``A`` stands for a letter and ``9`` for a
            digit; every other character is kept as written. ``uuid`` gives
            random UUIDs. A mapping gives
            several shapes, each with its share of the distinct values, as
            the penguin sample numbers ``9``, ``99`` and ``999`` need.
        distinct: Count of distinct values, which then repeat over the rows.
            ``None`` makes every value unique.
        as_number: Write each value as a number cell in a workbook, as a
            delivery with integer identifiers does. The pattern then holds
            digits only.
        link: A column of unique keys in another table. The values then
            come from those keys, so a join of the two synthetic tables finds
            each key, as each admission finds its patient.
    """

    kind: Literal["identifier"]
    pattern: Annotated[str, Field(min_length=1)] | Annotated[dict[Annotated[str, Field(min_length=1)], Share],
                                                             Field(min_length=1)]
    distinct: int | None = Field(default=None, ge=1)
    as_number: bool = False
    link: Link | None = None

    @property
    def patterns(self) -> dict[str, float]:
        """Return each shape with its share of the distinct values.

        Returns:
            The mapping; a single shape has the share 1.
        """
        return {self.pattern: 1.0} if isinstance(self.pattern, str) else dict(self.pattern)

    @model_validator(mode="after")
    def check_patterns(self) -> IdentifierColumn:
        """Check the shares of several shapes, and the digits of a number identifier.

        Returns:
            The unchanged column.

        Raises:
            ValueError: If the shares of several shapes sum too far from 1, or
                if ``as_number`` is set and a pattern holds another character
                than a digit.
        """
        total = sum(self.patterns.values())
        if abs(total - 1.0) > SHARE_TOLERANCE:
            raise ValueError(f"pattern: the shape shares sum to {total:.3g}, not 1")
        for pattern in self.patterns:
            if self.as_number and not pattern.isdigit():
                raise ValueError(f"as_number: the pattern {pattern!r} must hold digits only")
        return self


class NumericColumn(ColumnBase):
    """A column of numbers, recorded as rounded quantiles.

    Attributes:
        quantiles: Values at evenly spaced probabilities from 0 to 1. Two values
            give a uniform range; eleven give the deciles.
        tokens: Non-numeric texts, such as ``"NA"`` or ``"<LOD"``, and the
            share of the rows, empty rows excluded, that hold each.
        exact: Values that occur often, such as 0 or 100, and their share of
            the numbers.
        decimals: Decimal places of a written number. ``None`` keeps four
            significant digits.
        as_text: Write numbers as text cells in a workbook.
        scale: In the model mode, ``linear`` makes an effect multiply the value
            and ``log2`` makes it add the log2 fold change.
        suffix: Text after each number, such as ``%``. A cell then holds text,
            as ``73%``.
    """

    kind: Literal["numeric"]
    quantiles: list[float] = Field(min_length=2)
    tokens: dict[str, Share] = Field(default_factory=dict)
    exact: dict[float, Share] = Field(default_factory=dict)
    decimals: int | None = Field(default=None, ge=0, le=12)
    as_text: bool = False
    scale: Literal["linear", "log2"] = "linear"
    suffix: str | None = Field(default=None, min_length=1, max_length=3)

    check_quantiles = field_validator("quantiles")(check_non_decreasing)


class CategoricalColumn(ColumnBase):
    """A column with a small set of levels.

    Attributes:
        levels: Each level and its share of the filled cells.
    """

    kind: Literal["categorical"]
    levels: dict[str, Share] = Field(min_length=1)

    @field_validator("levels")
    @classmethod
    def check_shares_sum_to_one(cls, levels: dict[str, float]) -> dict[str, float]:
        """Check that the level shares sum to 1 within the rounding tolerance.

        Args:
            levels: Each level and its share.

        Returns:
            The unchanged levels.

        Raises:
            ValueError: If the shares sum too far from 1.
        """
        total = sum(levels.values())
        if abs(total - 1.0) > SHARE_TOLERANCE:
            raise ValueError(f"level shares sum to {total:.3g}, not 1")
        return levels


class TextColumn(ColumnBase):
    """A free text column, recorded as a length range and its common shapes.

    Attributes:
        lengths: Shortest and longest value length, in characters.
        shapes: The most common shapes, such as ``A99`` for a cabin, each with
            its share of the filled cells. The rest of the cells hold invented
            words of the length range.
        provider: A Faker provider, such as ``email`` or ``name``, which
            gives real-looking values instead of invented words. A value
            ``module:Class.method`` names a user Faker provider. It needs the
            extra ``sdg[faker]``.
        locale: The Faker locale of the provider, such as ``fr_FR``. None
            uses ``en_US``.
    """

    kind: Literal["text"]
    lengths: tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=0)]]
    shapes: dict[Annotated[str, Field(min_length=1)], Share] = Field(default_factory=dict)
    provider: str | None = Field(default=None, min_length=1)
    locale: str | None = Field(default=None, min_length=2)

    @model_validator(mode="after")
    def check_provider(self) -> TextColumn:
        """Check that the provider and the locale exist, when Faker is installed.

        Returns:
            The unchanged column.

        Raises:
            ValueError: If the provider or the locale does not exist, or if a
                locale has no provider.
        """
        if self.locale and not self.provider:
            raise ValueError("locale: a locale needs a provider")
        if self.provider:
            fake_values.check_provider(self.provider, self.locale)
        return self

    @field_validator("shapes")
    @classmethod
    def check_shape_shares(cls, shapes: dict[str, float]) -> dict[str, float]:
        """Check that the shape shares do not sum above 1.

        Args:
            shapes: Each shape and its share.

        Returns:
            The unchanged shapes.

        Raises:
            ValueError: If the shares sum above 1 beyond the rounding tolerance.
        """
        total = sum(shapes.values())
        if total > 1.0 + SHARE_TOLERANCE:
            raise ValueError(f"shape shares sum to {total:.3g}, more than 1")
        return shapes

    @field_validator("lengths")
    @classmethod
    def check_length_order(cls, lengths: tuple[int, int]) -> tuple[int, int]:
        """Check that the shortest length does not exceed the longest.

        Args:
            lengths: Shortest and longest length.

        Returns:
            The unchanged lengths.

        Raises:
            ValueError: If the shortest length is larger than the longest.
        """
        if lengths[0] > lengths[1]:
            raise ValueError("the first length must not exceed the second")
        return lengths


class DateColumn(ColumnBase):
    """A date column, recorded as intervals from an invented anchor date.

    No real date is ever recorded. The generator adds the intervals to the
    anchor, so the spacing of dates survives but their position does not.

    Attributes:
        anchor: Invented date that the intervals count from.
        interval_days: Quantiles of the days between the anchor and a value.
            With ``after``, the days after the date of the earlier column.
        date_format: ``strftime`` format of a date written as text.
        as_text: Write dates as text cells in a workbook. A text file always
            holds text.
        after: Name of an earlier date column of the sheet. Each date then
            comes on or after the date of that column in its row, as a
            discharge comes after its admission.
    """

    kind: Literal["date"]
    anchor: dt.date = dt.date(2000, 1, 1)
    interval_days: list[Annotated[float, Field(ge=0)]] = Field(min_length=2)
    date_format: str = "%Y-%m-%d"
    as_text: bool = False
    after: str | None = Field(default=None, min_length=1)

    check_intervals = field_validator("interval_days")(check_non_decreasing)


class BooleanColumn(ColumnBase):
    """A true or false column.

    Attributes:
        true_share: Share of the filled cells that are true.
        true_text: Text of a true cell in a text file, as delivered.
        false_text: Text of a false cell in a text file, as delivered.
    """

    kind: Literal["boolean"]
    true_share: Share
    true_text: str = "TRUE"
    false_text: str = "FALSE"


class DesignColumn(ColumnBase):
    """A column that the model mode fills from the study design.

    Attributes:
        field: ``subject``, ``group``, ``timepoint``, ``sample`` or
            ``replicate`` take the value of the design row. In the long layout,
            ``feature`` takes the feature name and ``value`` its value.
    """

    kind: Literal["design"]
    field: Literal["subject", "group", "timepoint", "sample", "replicate", "feature", "value"]


class FeatureSpec(StrictModel):
    """One feature of a sheet in the long layout.

    Attributes:
        name: Feature name, as the feature column writes it.
        quantiles: Values at evenly spaced probabilities from 0 to 1.
        scale: ``linear`` or ``log2``, as for a numeric column.
        decimals: Decimal places of a written value.
    """

    name: str = Field(min_length=1)
    quantiles: list[float] = Field(min_length=2)
    scale: Literal["linear", "log2"] = "linear"
    decimals: int | None = Field(default=None, ge=0, le=12)

    check_quantiles = field_validator("quantiles")(check_non_decreasing)


Column = Annotated[
    IdentifierColumn | NumericColumn | CategoricalColumn | TextColumn | DateColumn | BooleanColumn | DesignColumn,
    Field(discriminator="kind"),
]

class Correlation(StrictModel):
    """The rank correlation of two numeric columns of a sheet.

    Attributes:
        columns: The names of the two numeric columns.
        rho: The Spearman rank correlation, from -1 to 1.
    """

    columns: tuple[Annotated[str, Field(min_length=1)], Annotated[str, Field(min_length=1)]]
    rho: float = Field(ge=-1.0, le=1.0)


#: Design fields that only the long layout has.
LONG_FIELDS = ("feature", "value")


class Sheet(StrictModel):
    """One table: a CSV or TSV file, or one sheet of a workbook.

    Column names may repeat, because a delivered file can repeat them and the
    synthetic file must too.

    A sheet with a ``design`` column draws its rows from the study design in
    the model mode; its row count follows from the design. Any other sheet
    needs ``rows``.

    Attributes:
        name: Sheet name. A CSV or TSV file ignores it.
        rows: Number of data rows, empty rows included. A design sheet has none.
        empty_rows: Number of data rows with every cell empty. A design sheet
            adds them to its design rows.
        columns: Columns in delivered order.
        layout: ``wide``: one row per sample, each numeric column a feature.
            ``long``: one row per sample and feature, with a feature column and
            a value column.
        features: The features of a long sheet.
        preamble: Rows above the header, such as a title, comment lines or a
            second header row.
        footer: Comment rows below the data.
        correlations: Rank correlations of pairs of numeric columns, which a
            Gaussian copula keeps. A sheet with design columns has none.
    """

    name: str = Field(default="sheet_1", min_length=1)
    rows: int | None = Field(default=None, ge=0)
    empty_rows: int = Field(default=0, ge=0)
    columns: list[Column] = Field(min_length=1)
    layout: Literal["wide", "long"] = "wide"
    features: list[FeatureSpec] = Field(default_factory=list)
    preamble: list[NoteRow] = Field(default_factory=list)
    footer: list[NoteRow] = Field(default_factory=list)
    correlations: list[Correlation] = Field(default_factory=list)

    @property
    def design_fields(self) -> list[str]:
        """Return the design fields of the sheet, in column order.

        Returns:
            The ``field`` of each design column.
        """
        return [column.field for column in self.columns if column.kind == "design"]

    @model_validator(mode="after")
    def check_rows_and_layout(self) -> Sheet:
        """Check the row count and the layout against the design columns.

        Returns:
            The unchanged sheet.

        Raises:
            ValueError: If a plain sheet has no row count, if its empty rows
                exceed its rows, or if the layout and the design fields disagree.
        """
        self.check_date_order()
        self.check_correlations()
        fields = self.design_fields
        if not fields:
            if self.rows is None:
                raise ValueError("rows: a sheet without design columns needs a row count")
            if self.empty_rows > self.rows:
                raise ValueError(f"empty_rows ({self.empty_rows}) exceeds rows ({self.rows})")
            if self.layout == "long" or self.features:
                raise ValueError("layout: the long layout needs design columns")
            return self
        if self.layout == "long":
            for name in LONG_FIELDS:
                if fields.count(name) != 1:
                    raise ValueError(f"layout: a long sheet needs exactly one design column of field {name!r}")
            if not self.features:
                raise ValueError("features: a long sheet needs at least one feature")
        elif any(name in fields for name in LONG_FIELDS) or self.features:
            raise ValueError("layout: feature and value columns, and features, need the long layout")
        return self

    def check_correlations(self) -> None:
        """Check that each correlation names two numeric columns of a sheet without design columns.

        Raises:
            ValueError: If a name is missing or is not a numeric column, if the
                two names are the same, if a pair repeats, or if the sheet has
                design columns.
        """
        if self.correlations and self.design_fields:
            raise ValueError("correlations: a sheet with design columns has no correlations")
        kinds = {column.name: column.kind for column in self.columns}
        seen = set()
        for index, correlation in enumerate(self.correlations):
            pair = frozenset(correlation.columns)
            if len(pair) != 2 or pair in seen:
                raise ValueError(f"correlations[{index}].columns: give two different columns, once")
            seen.add(pair)
            for name in correlation.columns:
                if kinds.get(name) != "numeric":
                    raise ValueError(f"correlations[{index}].columns: {name!r} is not a numeric column")

    def check_date_order(self) -> None:
        """Check that each ``after`` field names an earlier date column.

        Raises:
            ValueError: If the named column is missing, comes later or is not a date column.
        """
        for index, column in enumerate(self.columns):
            if column.kind != "date" or column.after is None:
                continue
            earlier = [item for item in self.columns[:index] if item.name == column.after]
            if not earlier:
                raise ValueError(f"columns[{index}].after: no earlier column is named {column.after!r}")
            if earlier[0].kind != "date":
                raise ValueError(f"columns[{index}].after: column {column.after!r} is not a date column")

    def feature_names(self) -> list[str]:
        """Return the features of a design sheet, which effects can target.

        Returns:
            The feature names of a long sheet, the numeric column names of a
            wide design sheet, and nothing for a plain sheet.
        """
        if not self.design_fields:
            return []
        if self.layout == "long":
            return [feature.name for feature in self.features]
        return [column.name for column in self.columns if column.kind == "numeric"]


class TabularDataset(DatasetBase):
    """A CSV file, a TSV file, an Excel workbook or a Parquet file.

    Attributes:
        format: ``csv``, ``tsv``, ``excel`` or ``parquet``.
        sheets: The tables. A CSV, TSV or Parquet file has exactly one.
        delimiter: Delimiter of a text file. ``None`` uses a comma for CSV and
            a tab for TSV.
    """

    format: Literal["csv", "tsv", "excel", "parquet", "rds"]
    sheets: list[Sheet] = Field(min_length=1)
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)

    @model_validator(mode="after")
    def check_sheets(self) -> TabularDataset:
        """Check the sheet count, the sheet names and the parts of a Parquet file.

        Returns:
            The unchanged dataset.

        Raises:
            ValueError: If a CSV, TSV or Parquet file has more than one sheet,
                if two sheets share a name, or if a Parquet file has a
                delimiter or rows above or below its table.
        """
        if self.format != "excel" and len(self.sheets) != 1:
            raise ValueError(f"a {self.format} dataset has exactly one sheet")
        names = [sheet.name for sheet in self.sheets]
        if len(names) != len(set(names)):
            raise ValueError("sheet names must be unique")
        if self.format in TABLE_FORMATS:
            if self.delimiter is not None:
                raise ValueError(f"delimiter: a {self.format} dataset has no delimiter")
            if self.sheets[0].preamble or self.sheets[0].footer:
                raise ValueError(f"sheets: a {self.format} table has no preamble and no footer rows")
        return self

    def link_error(self, datasets: dict[str, Any]) -> str | None:
        """Check that each link names a column of unique keys of another table.

        Args:
            datasets: Every dataset of the config, by name.

        Returns:
            The first error, with the path of the field, or None.
        """
        for sheet_index, sheet in enumerate(self.sheets):
            for index, column in enumerate(sheet.columns):
                if column.kind != "identifier" or column.link is None:
                    continue
                path = f"sheets[{sheet_index}].columns[{index}].link"
                target = link_target(column.link, datasets)
                if isinstance(target, str):
                    return f"{path}: {target}"
                if target[0] is sheet:
                    return f"{path}: a link names a column of another table"
        return None

    def feature_names(self) -> set[str]:
        """Return the features that an effect can target.

        Returns:
            The features of every design sheet.
        """
        return {name for sheet in self.sheets for name in sheet.feature_names()}

    def uses_design(self) -> bool:
        """Say whether a sheet draws its rows from the study design.

        Returns:
            True when a sheet has a design column.
        """
        return any(sheet.design_fields for sheet in self.sheets)

    def mode_error(self, mode: str) -> str | None:
        """Reject design columns in the resample mode.

        Args:
            mode: ``resample`` or ``model``.

        Returns:
            The error, or None.
        """
        if mode == "resample" and self.uses_design():
            return "sheets: design columns need the model mode"
        return None

    def describe(self) -> dict[str, Any]:
        """Summarize the dataset for ``sdg check``.

        Returns:
            The base summary with the sheet, row and column counts.
        """
        summary = super().describe()
        summary["sheets"] = len(self.sheets)
        summary["rows"] = "design" if self.uses_design() else sum(sheet.rows for sheet in self.sheets)
        summary["columns"] = sum(len(sheet.columns) for sheet in self.sheets)
        return summary


#: Dataset models that this module registers.
DATASET_MODELS = (TabularDataset,)


def link_target(link: Link, datasets: dict[str, Any]) -> tuple[Sheet, int, Any] | str:
    """Find the sheet and the column that a link names.

    Args:
        link: The link.
        datasets: Every dataset of the config, by name.

    Returns:
        The sheet, the column position and the dataset, or the reason that
        the link is broken.
    """
    dataset = datasets.get(link.dataset)
    if not isinstance(dataset, TabularDataset):
        return f"no CSV, TSV, Excel or Parquet dataset is named {link.dataset!r}"
    sheets = [sheet for sheet in dataset.sheets if link.sheet is None or sheet.name == link.sheet]
    if not sheets:
        return f"dataset {link.dataset!r} has no sheet {link.sheet!r}"
    sheet = sheets[0]
    if sheet.design_fields:
        return "a link names a sheet without design columns"
    positions = [index for index, column in enumerate(sheet.columns) if column.name == link.column]
    if not positions or sheet.columns[positions[0]].kind != "identifier":
        return f"sheet {sheet.name!r} has no identifier column {link.column!r}"
    if sheet.columns[positions[0]].link is not None:
        return f"column {link.column!r} has a link itself"
    return sheet, positions[0], dataset


def format_of(path: Path, table: Table) -> str:
    """Return the format name of a delivered table.

    Args:
        path: The file.
        table: One table of the file.

    Returns:
        ``excel``, ``parquet``, ``tsv`` or ``csv``.
    """
    suffix = privacy.file_suffix(path)
    if suffix in WORKBOOK_SUFFIXES:
        return "excel"
    if suffix in (".parquet", ".rds"):
        return suffix[1:]
    return "tsv" if table.delimiter == "\t" else "csv"


def table_label(file_label: str, sheet: str | None) -> str:
    """Return the label of one table in a report.

    Args:
        file_label: Generic label of the file.
        sheet: Sheet name, or None for a text file.

    Returns:
        The file label, with the sheet for a workbook.
    """
    return file_label if sheet is None else f"{file_label} | sheet {sheet!r}"


def empty_row_count(table: Table) -> int:
    """Count the data rows with every cell empty.

    Args:
        table: The table.

    Returns:
        The count.
    """
    return sum(all(is_empty(cell) for cell in row) for row in table.rows)


def is_private_table(path: Path, table: Table, options: privacy.Options) -> bool:
    """Say whether a table records counts only (privacy rule 2).

    Args:
        path: The file.
        table: The table.
        options: The glimpse or profile options.

    Returns:
        True when the file name matches the private pattern or a name marks a
        person-level field. The names are the header, the cells above it, and
        the first data row when it holds labels only: if the header search
        takes a row of codes, such as ``X1`` to ``X23`` of the UCI credit
        workbook, as the header, the names ``SEX`` and ``AGE`` are in that row.
    """
    names = [*table.header, *(cell for row in table.preamble for cell in row)]
    if table.rows and is_label_row(table.rows[0]):
        names += table.rows[0]
    return bool(options.private_files.search(path.name)) or any(
        privacy.is_private_name(name) for name in names if not is_empty(name))


def kept_table(table: Table, options: privacy.Options) -> Table:
    """Leave out the columns that ``--drop`` names.

    Args:
        table: The table as read.
        options: The options, with the drop pattern.

    Returns:
        The table without the matching columns, or the same table.
    """
    if options.drop is None:
        return table
    kept = [index for index, name in enumerate(table.header) if not options.drop.search(str(name))]
    if len(kept) == len(table.header):
        return table
    return Table(table.sheet, [table.header[index] for index in kept],
                 [[row[index] for index in kept] for row in table.rows],
                 table.delimiter, table.ragged, table.preamble, table.footer)


def private_positions(table: Table) -> set[int]:
    """Find the private columns of a table, which record counts only.

    A column is private when its header cell, a cell above it, or its cell in
    a first data row of labels holds a private name, such as ``age`` or
    ``sex``. The other columns of a private table record safe facts only.

    Args:
        table: The table.

    Returns:
        The positions of the private columns.
    """
    rows = [table.header, *table.preamble]
    if table.rows and is_label_row(table.rows[0]):
        rows.append(table.rows[0])
    return {index for index in range(len(table.header))
            if any(index < len(row) and not is_empty(row[index]) and privacy.is_private_name(row[index])
                   for row in rows)}


def date_order(rows: list[list[object]], columns: list[ColumnStats]) -> dict[int, tuple[int, list[float]]]:
    """Find each date column that comes on or after an earlier date column in every row.

    A discharge date comes on or after its admission date in each row, so the
    synthetic discharge must too, or code that computes a length of stay gets
    negative days. Among the earlier columns that hold, the closest one wins:
    the one with the smallest median distance.

    Args:
        rows: The data rows.
        columns: The statistics of each column.

    Returns:
        Each later column position mapped to the earlier column position and
        the days between the two dates of each row. A pair needs
        ``MIN_VALUES`` rows with both dates.
    """
    dates = {index: [parse_date(row[index], stats.date_format) if index < len(row) else None for row in rows]
             for index, stats in enumerate(columns) if stats.kind == "date"}
    found: dict[int, tuple[int, list[float]]] = {}
    for later in dates:
        best: tuple[float, int, list[float]] | None = None
        for earlier in (index for index in dates if index < later):
            days = [(end - start).total_seconds() / 86400 for start, end in zip(dates[earlier], dates[later],
                                                                                    strict=True)
                    if start is not None and end is not None]
            if len(days) >= privacy.MIN_VALUES and min(days) >= 0:
                middle = float(np.median(days))
                if best is None or middle < best[0]:
                    best = (middle, earlier, days)
        if best is not None:
            found[later] = (best[1], best[2])
    return found


def correlation_strength(pair: tuple[int, int, float]) -> float:
    """Return the absolute correlation of a pair, the key that sorts the strongest pairs first.

    Args:
        pair: Two column positions and their correlation.

    Returns:
        The absolute correlation.
    """
    return abs(pair[2])


def rank_correlations(rows: list[list[object]], columns: list[ColumnStats],
                      excluded: set[int]) -> list[tuple[int, int, float]]:
    """Find the rank correlations that a table records (rule 10).

    Args:
        rows: The data rows.
        columns: The statistics of each column.
        excluded: Positions of the private columns, which record no correlation.

    Returns:
        Each pair of numeric column positions with its Spearman correlation,
        rounded, strongest first. A pair needs ``TRIM_MIN_VALUES`` rows with two
        numbers and an absolute correlation of ``MIN_CORRELATION`` or more.
    """
    numeric = [index for index, stats in enumerate(columns) if stats.kind == "numeric" and index not in excluded]
    if len(numeric) > privacy.CORRELATION_COLUMNS:
        return []
    values = {index: [number(without_suffix(row[index])) if index < len(row) else None for row in rows]
              for index in numeric}
    found = []
    for first_position, first in enumerate(numeric):
        for second in numeric[first_position + 1:]:
            pairs = [(left, right) for left, right in zip(values[first], values[second], strict=True)
                     if left is not None and right is not None]
            if len(pairs) < privacy.TRIM_MIN_VALUES:
                continue
            left, right = zip(*pairs, strict=True)
            if len(set(left)) < 2 or len(set(right)) < 2:
                continue
            rho = round(float(scipy_stats.spearmanr(left, right).statistic), privacy.CORRELATION_DECIMALS)
            if abs(rho) >= privacy.MIN_CORRELATION:
                found.append((first, second, rho))
    return sorted(found, key=correlation_strength, reverse=True)[:privacy.MAX_CORRELATIONS]


def correlation_text(pairs: list[tuple[int, int, float]], columns: list[ColumnStats],
                     names: dict[object, str]) -> str:
    """Write the correlations of a table for its report.

    Args:
        pairs: The correlations of :func:`rank_correlations`.
        columns: The statistics of each column.
        names: Generic name of each column, empty without generic labels.

    Returns:
        A text such as ``'x' ~ 'y' 0.75, 'a' ~ 'b' -0.32``.
    """
    shown = []
    for first, second, rho in pairs:
        left, right = columns[first].name, columns[second].name
        shown.append(f"{names.get(left, left)!r} ~ {names.get(right, right)!r} {rho:g}")
    return ", ".join(shown)


def order_facts(days: list[float], earlier: str, private: bool) -> list[Fact]:
    """Report the date order of a later date column.

    Args:
        days: The days after the earlier date, one per row.
        earlier: Name of the earlier column, as the report prints it.
        private: The table is private, so the days do not print.

    Returns:
        The ``after`` fact, and the days for a table that is not private.
    """
    facts = [Fact("after", repr(earlier))]
    if not private:
        facts.append(Fact("days after", f"{privacy.format_number(min(days))} to {privacy.format_number(max(days))}"
                                        f", median {privacy.format_number(float(np.median(days)))}"))
    return facts


def private_facts(table: Table, names: dict[object, str]) -> list[Fact]:
    """Report how a private table applies rule 2.

    Args:
        table: The private table.
        names: Generic name of each column, empty without generic labels.

    Returns:
        The ``private`` fact, and the ``private columns`` fact when a column is private.
    """
    facts = [Fact("private", "safe facts")]
    shown = [repr(names.get(table.header[index], table.header[index])) for index in sorted(private_positions(table))]
    if shown:
        facts.append(Fact("private columns", ", ".join(shown)))
    return facts


def glimpse_table(path: Path, label: str, table: Table, sheet: str | None,
                  options: privacy.Options, kept: frozenset[int] = frozenset()) -> TableReport:
    """Report one table under the privacy rules.

    Args:
        path: The file, read for its name only.
        label: Generic label of the file.
        table: The table.
        sheet: Sheet name to print, which can be generic.
        options: The glimpse options.
        kept: Positions of the columns that keep their names under generic
            labels, such as the role columns of an Olink file. They count in
            the numbering of their kind, as in the profile, and stay out of
            the label key.

    Returns:
        The table report.
    """
    private = is_private_table(path, table, options)
    strict = private_positions(table) if private else set()
    columns = [analyze_column(name, table.column(index), options.max_categories)
               for index, name in enumerate(table.header)]
    names = generic_columns(columns, options.generic_labels)
    for index in kept:
        names.pop(columns[index].name, None)
    named = [stats for index, stats in enumerate(columns) if index not in kept]
    # A private table prints generic levels, so the key maps them, as in the profile.
    note_columns(options, label, sheet, named, names, private or options.generic_labels)
    facts = [Fact("rows", str(len(table.rows))), Fact("columns", str(len(table.header))),
             Fact("empty rows", str(empty_row_count(table)))]
    if table.delimiter:
        facts.append(Fact("delimiter", repr(table.delimiter)))
    facts += layout_facts(table.preamble, table.footer)
    if private:
        facts += private_facts(table, names)
    correlations = rank_correlations(table.rows, columns, strict)
    if correlations:
        facts.append(Fact("correlations", correlation_text(correlations, columns, names)))
    repeated = [name for name, count in Counter(table.header).items() if count > 1]
    if repeated:
        facts.append(Fact("repeated names", ", ".join(repr(names.get(name, name)) for name in repeated)))
    if table.ragged:
        facts.append(Fact("ragged rows", str(table.ragged)))
    report = TableReport(table_label(label, sheet), format_of(path, table), facts)
    order = date_order(table.rows, columns)
    for index, stats in enumerate(columns):
        generic = (options.generic_labels or private) and stats.kind == "categorical"
        report.columns.append(column_report(stats, private, options.max_categories, names.get(stats.name),
                                            level_names(stats) if generic else None,
                                            safe=private and index not in strict))
        if index in order:
            earlier = columns[order[index][0]].name
            report.columns[-1].facts += order_facts(order[index][1], names.get(earlier, earlier), private)
    return report


def key_links(tables: list[tuple[Table, list[str]]]) -> dict[tuple[int, int], tuple[int, int]]:
    """Find the identifier columns whose values are all keys of another table.

    A key column holds unique values. An identifier column of another table
    links to it when each of its values is a key, as each admission names a
    patient of the patient table. A linked column is never a key column of a
    link, and two key columns with the same values link the later table to
    the earlier one.

    Args:
        tables: Each table with the kind of each column.

    Returns:
        Each linked column, as table and column positions, mapped to its key
        column. A link needs ``MIN_VALUES`` distinct values.
    """
    values: dict[tuple[int, int], set[str]] = {}
    unique: set[tuple[int, int]] = set()
    for table_index, (table, kinds) in enumerate(tables):
        for index, kind in enumerate(kinds):
            if kind == "identifier":
                cells = [str(cell) for cell in table.column(index) if not is_empty(cell)]
                values[(table_index, index)] = set(cells)
                if cells and len(set(cells)) == len(cells):
                    unique.add((table_index, index))
    links: dict[tuple[int, int], tuple[int, int]] = {}
    for child, found in values.items():
        if len(found) < privacy.MIN_VALUES:
            continue
        for parent in sorted(unique):
            same = child in unique and values[parent] == found
            if (parent[0] != child[0] and parent not in links and child not in links.values()
                    and found <= values[parent] and (not same or parent[0] < child[0])):
                links[child] = parent
                break
    return links


def glimpse(paths: list[Path], labels: list[str], options: privacy.Options) -> list[TableReport]:
    """Report every table of the given CSV, TSV, Excel and Parquet files.

    An identifier column whose values are all keys of another table prints
    ``keys of`` with that table and column.

    Args:
        paths: The files.
        labels: Generic label of each file.
        options: The glimpse options.

    Returns:
        One table report per text file and per sheet.
    """
    reports, tables = [], []
    for path, label in zip(paths, labels, strict=True):
        read = [kept_table(table, options) for table in read_tables(path)]
        sheets = privacy.generic_names([table.sheet for table in read if table.sheet is not None], "sheet")
        if options.generic_labels:
            privacy.note_key(options, "sheet", label, None, "", sheets)
        for table in read:
            sheet = sheets.get(table.sheet) if options.generic_labels and table.sheet else table.sheet
            reports.append(glimpse_table(path, label, table, sheet, options))
            tables.append(table)
    links = key_links([(table, [column.kind for column in report.columns])
                       for table, report in zip(tables, reports, strict=True)])
    for (table_index, index), (parent_table, parent_index) in links.items():
        parent = reports[parent_table]
        reports[table_index].columns[index].facts.append(
            Fact("keys of", f"{parent.label} {parent.columns[parent_index].name!r}"))
    return reports



#: A number that is at least this share of a numeric column is an exact value.
EXACT_SHARE = 0.05

#: Most shapes that a profile keeps for an identifier column.
MAX_SHAPES = 10

#: Defaults that a profile writes where the privacy rules forbid a measurement.
DEFAULT_PATTERN = "SUBJ-999"
DEFAULT_QUANTILES = [0.0, 100.0]
DEFAULT_LENGTHS = [1, 20]
DEFAULT_INTERVALS = [0.0, 365.0]
DEFAULT_TRUE_SHARE = 0.5

#: Column kinds whose every value is sensitive, and the name of each for an audit.
AUDITED_KINDS = {"identifier": "identifier", "text": "free text", "date": "date"}


def share(count: float, total: float) -> float:
    """Return a share rounded to four decimals.

    Args:
        count: The part.
        total: The whole.

    Returns:
        ``count / total``, or 0 when the whole is 0.
    """
    return round(float(count) / float(total), 4) if total else 0.0


def level_shares(counts: Counter, names: dict[str, str] | None = None) -> dict[str, float]:
    """Turn level counts into shares that sum to exactly 1.

    Rounding can leave the sum a little off 1, so the most common level takes
    the difference.

    Args:
        counts: Each level and its count.
        names: Generic name of each level, or None to keep the real levels.

    Returns:
        Each level, most common first, with its share.
    """
    total = sum(counts.values())
    shares = {(names or {}).get(level, level): share(count, total) for level, count in counts.most_common()}
    first = next(iter(shares))
    shares[first] = round(shares[first] + 1.0 - sum(shares.values()), 4)
    return shares


def identifier_patterns(stats: ColumnStats) -> str | dict[str, float]:
    """Return the shapes of the distinct values of an identifier column.

    A column of UUIDs gives ``uuid``. One shape gives a text. Several shapes
    give a mapping, most common first, with the share of the distinct values
    of each, for the ``MAX_SHAPES`` most common shapes. The penguin sample numbers 1 to 152 kept only the shape
    ``99`` in version 0.8.0, so the synthetic column added suffixes such as
    ``03_132`` and became text.

    Args:
        stats: The column statistics.

    Returns:
        The ``pattern`` field.
    """
    if stats.values and all(fake_values.UUID.fullmatch(value.strip()) for value in stats.values):
        return UUID_PATTERN
    shapes = Counter(dict(Counter(privacy.code_shape(value) for value in stats.values).most_common(MAX_SHAPES)))
    if len(shapes) == 1:
        return next(iter(shapes))
    return level_shares(shapes)


def identifier_fields(stats: ColumnStats, private: bool, workbook: bool) -> tuple[dict[str, Any], list[str]]:
    """Profile an identifier column: the shapes of its values and its distinct count.

    A workbook column of integer cells, such as the ``ID`` column of the UCI
    credit workbook, keeps its number cells. In a private table, its default
    pattern has the digits of the row count, which the report prints.

    Args:
        stats: The column statistics.
        private: The table records counts only.
        workbook: The column comes from a workbook.

    Returns:
        The column fields and the names of the fields that hold defaults.
    """
    as_number = workbook and stats.filled > 0 and stats.cell_types.get("int", 0) == stats.filled
    if private or not stats.filled:
        if as_number:
            return {"pattern": "9" * len(str(stats.filled)), "as_number": True}, ["pattern"]
        return {"pattern": DEFAULT_PATTERN}, ["pattern"]
    fields: dict[str, Any] = {"pattern": identifier_patterns(stats)}
    if stats.unique < stats.filled:
        fields["distinct"] = stats.unique
    if as_number:
        fields["as_number"] = True
    return fields, []


def safe_number_fields(stats: ColumnStats) -> tuple[dict[str, Any], list[str]]:
    """Profile the values of a numeric column of a private table that is not a private column.

    A column of few numbers keeps its codes, each with 10 cells or more, as
    exact values with their shares, so a column of 0 and 1 stays 0 and 1.
    A column of 50 numbers or more keeps its quantiles from 5% to 95%. Other
    columns get the default quantiles.

    Args:
        stats: The column statistics.

    Returns:
        The ``quantiles`` field, with ``exact`` for codes, and the names of the
        fields that hold defaults.
    """
    codes = kept_codes(stats)
    if codes:
        rounded: Counter = Counter()
        for code, count in codes.items():
            rounded[privacy.round_sig(code)] += count
        return {"quantiles": [min(rounded), max(rounded)], "exact": level_shares(rounded)}, []
    if codes is None and len(stats.numbers) >= privacy.TRIM_MIN_VALUES:
        return {"quantiles": trimmed_quantiles(stats.numbers)}, []
    return {"quantiles": list(DEFAULT_QUANTILES)}, ["quantiles"]


def numeric_fields(stats: ColumnStats, private: bool, workbook: bool,
                   safe: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Profile a numeric column: quantiles, tokens, exact values and decimals.

    Args:
        stats: The column statistics.
        private: The table is private (rule 2).
        workbook: The column comes from a workbook or a Parquet file.
        safe: In a private table, the column is not a private column, so it
            keeps its safe facts.

    Returns:
        The column fields and the names of the fields that hold defaults.
    """
    if private and safe:
        fields, defaults = safe_number_fields(stats)
    elif private or len(stats.numbers) < privacy.MIN_VALUES:
        fields: dict[str, Any] = {"quantiles": list(DEFAULT_QUANTILES)}
        defaults = ["quantiles"]
    else:
        fields = {"quantiles": rounded_quantiles(stats.numbers)}
        defaults = []
        counts = Counter(stats.numbers)
        exact = {privacy.round_sig(value): share(count, len(stats.numbers))
                 for value, count in counts.most_common() if count > 1 and count >= EXACT_SHARE * len(stats.numbers)}
        if exact:
            fields["exact"] = exact
    kept = {token: count for token, count in stats.tokens.most_common()
            if not private or privacy.is_missing_token(token)}
    if kept:
        fields["tokens"] = {token: share(count, stats.rows) for token, count in kept.items()}
    if stats.decimals is not None:
        fields["decimals"] = stats.decimals
    if stats.suffix:
        fields["suffix"] = stats.suffix
    elif workbook and stats.as_text:
        fields["as_text"] = True
    return fields, defaults


def categorical_fields(stats: ColumnStats, private: bool, generic_labels: bool) -> tuple[dict[str, Any], list[str]]:
    """Profile a categorical column: its levels and their shares.

    Args:
        stats: The column statistics.
        private: The table records counts only, so the levels become generic.
        generic_labels: Replace the levels with generic names.

    Returns:
        The column fields and the names of the fields that hold defaults.
    """
    names = level_names(stats) if private or generic_labels else None
    return {"levels": level_shares(stats.values, names)}, ["levels"] if private else []


def text_fields(stats: ColumnStats, private: bool, safe: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Profile a free text column: its length range and its common shapes (rule 6).

    The shapes are the ones that the glimpse report prints. The profile keeps
    them when they fill half of the cells or more, as the Titanic tickets and
    cabins do, so code that parses such a column reads the synthetic cells.
    A column whose name marks a person value, such as ``Email`` or ``City``,
    gets a Faker provider, which the column name chooses. A private table
    records it too, because it holds no value. A code provider, such as
    ``passport_number``, gives way to the common shapes, which hold the real
    form. A column of a private table that is not a private column keeps its
    common shapes too, because a shape holds no value.

    Args:
        stats: The column statistics.
        private: The table is private (rule 2).
        safe: In a private table, the column is not a private column.

    Returns:
        The column fields and the names of the fields that hold defaults.
    """
    if not stats.filled:
        return {"lengths": [0, 0]}, []
    provider = text_provider(stats)
    shapes = common_shapes(stats) if not provider and (safe or not private) else []
    fields: dict[str, Any] = {"provider": provider} if provider else {}
    if shapes:
        fields["shapes"] = {shape: share(count, stats.filled) for shape, count in shapes}
    if private:
        return {"lengths": list(DEFAULT_LENGTHS), **fields}, ["lengths"]
    lengths = [len(value) for value in stats.values]
    return {"lengths": [min(lengths), max(lengths)], **fields}, []


def date_fields(stats: ColumnStats, private: bool, workbook: bool) -> tuple[dict[str, Any], list[str]]:
    """Profile a date column: intervals from the first date, never a date (rule 7).

    Args:
        stats: The column statistics.
        private: The table records counts only.
        workbook: The column comes from a workbook or a Parquet file, whose
            cells keep their types.

    Returns:
        The column fields and the names of the fields that hold defaults.
    """
    fields: dict[str, Any] = {}
    defaults = []
    if private or len(stats.dates) < privacy.MIN_VALUES:
        fields["interval_days"] = list(DEFAULT_INTERVALS)
        defaults.append("interval_days")
    else:
        first = min(stats.dates)
        fields["interval_days"] = rounded_quantiles([(date - first).total_seconds() / 86400
                                                     for date in stats.dates])
    if stats.date_format:
        fields["date_format"] = stats.date_format
    if workbook and stats.as_text:
        fields["as_text"] = True
    return fields, defaults


def parquet_date_fields(stats: ColumnStats) -> dict[str, Any]:
    """Choose the date format of a Parquet date column from its cell type.

    A Parquet file has date columns and timestamp columns. The synthetic file
    writes a date column when ``date_format`` is ``%Y-%m-%d``, and a timestamp
    column otherwise. A timestamp column with every time at midnight gets
    ``%Y-%m-%d 00:00:00``, which draws whole days.

    Args:
        stats: The statistics of a date column of a Parquet file.

    Returns:
        The ``date_format`` field, or no field for a date column.
    """
    if not stats.cell_types.get("datetime"):
        return {}
    if all(date.time() == dt.time() for date in stats.dates):
        return {"date_format": f"{PARQUET_DATE} 00:00:00"}
    return {"date_format": PARQUET_DATETIME}


def boolean_fields(stats: ColumnStats, private: bool, safe: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Profile a boolean column: its true share and its texts.

    Args:
        stats: The column statistics.
        private: The table is private (rule 2).
        safe: In a private table, the column is not a private column. It then
            keeps its true share when the true and the false cells each number
            10 or more, as a code does.

    Returns:
        The column fields and the names of the fields that hold defaults.
    """
    rare = min(stats.true_count, stats.filled - stats.true_count) < privacy.MIN_VALUES
    if private and (not safe or rare):
        return {"true_share": DEFAULT_TRUE_SHARE}, ["true_share"]
    fields: dict[str, Any] = {"true_share": share(stats.true_count, stats.filled)}
    texts = Counter(value.strip() for value in stats.values.elements() if value.strip().lower() in BOOLEAN_TEXT)
    for text, _ in texts.most_common():
        key = "true_text" if text.lower() == "true" else "false_text"
        fields.setdefault(key, text)
    fields = {key: value for key, value in fields.items()
              if not (key == "true_text" and value == "TRUE") and not (key == "false_text" and value == "FALSE")}
    return fields, []


def column_fields(stats: ColumnStats, name: str, private: bool, generic_labels: bool,
                  workbook: bool, safe: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Profile one column under the privacy rules.

    Args:
        stats: The column statistics, from the rows that are not empty.
        name: Column name to write, which can be generic.
        private: The table is private (rule 2).
        generic_labels: Replace the levels with generic names.
        workbook: The column comes from a workbook or a Parquet file, whose
            cells keep their types.
        safe: In a private table, the column is not a private column, so it
            keeps its safe facts: identifier shapes, codes, quantiles from 5%
            to 95% and a true share.

    Returns:
        The column config and the names of the fields that hold defaults.
    """
    dropped = sum(count for token, count in stats.tokens.items() if not privacy.is_missing_token(token))
    missing = stats.empty + (dropped if private and stats.kind == "numeric" else 0)
    column: dict[str, Any] = {"name": name, "kind": stats.kind}
    if missing:
        column["missing"] = share(missing, stats.rows)
    if stats.kind == "identifier":
        fields, defaults = identifier_fields(stats, private and not safe, workbook)
    elif stats.kind == "numeric":
        fields, defaults = numeric_fields(stats, private, workbook, safe)
    elif stats.kind == "categorical":
        fields, defaults = categorical_fields(stats, private, generic_labels)
    elif stats.kind == "date":
        fields, defaults = date_fields(stats, private, workbook)
    elif stats.kind == "boolean":
        fields, defaults = boolean_fields(stats, private, safe)
    else:
        fields, defaults = text_fields(stats, private, safe)
    column.update(fields)
    return column, defaults


def sheet_config(path: Path, label: str, table: Table, sheet: str, options: privacy.Options,
                 workbook: bool) -> tuple[dict[str, Any], list[str]]:
    """Profile one table into a sheet config.

    The columns are profiled on the rows that are not empty, because the empty
    rows have their own count.

    Args:
        path: The file, read for its name only.
        label: Generic label of the file, for the label key.
        table: The table.
        sheet: Sheet name to write, which can be generic.
        options: The profile options.
        workbook: The table comes from a workbook or a Parquet file, whose
            cells keep their types.

    Returns:
        The sheet config and the paths of the fields that hold defaults.
    """
    private = is_private_table(path, table, options)
    strict = private_positions(table) if private else set()
    parquet = privacy.file_suffix(path) == ".parquet"
    content = [row for row in table.rows if not all(is_empty(cell) for cell in row)]
    stats_list = [analyze_column(name, [row[index] for row in content], options.max_categories)
                  for index, name in enumerate(table.header)]
    names = generic_columns(stats_list, options.generic_labels)
    note_columns(options, label, sheet if table.sheet else None, stats_list, names,
                 private or options.generic_labels)
    columns, defaults = [], []
    order = date_order(content, stats_list)
    for index, stats in enumerate(stats_list):
        column, defaulted = column_fields(stats, names.get(stats.name, stats.name), private,
                                          options.generic_labels, workbook, safe=private and index not in strict)
        if parquet and stats.kind == "date" and not stats.date_format:
            column.update(parquet_date_fields(stats))
        if index in order:
            earlier = stats_list[order[index][0]].name
            column["after"] = names.get(earlier, earlier)
            if not private:
                column["interval_days"] = rounded_quantiles(order[index][1])
        columns.append(column)
        defaults += [f"columns[{index}].{field_name}" for field_name in defaulted]
    config: dict[str, Any] = {"name": sheet, "rows": len(table.rows), "empty_rows": len(table.rows) - len(content)}
    correlations = rank_correlations(content, stats_list, strict)
    if table.preamble:
        config["preamble"] = [note_fields(row) for row in table.preamble]
    if table.footer:
        config["footer"] = [note_fields(row) for row in table.footer]
    config["columns"] = columns
    if correlations:
        config["correlations"] = [{"columns": [names.get(stats_list[first].name, stats_list[first].name),
                                               names.get(stats_list[second].name, stats_list[second].name)],
                                   "rho": rho} for first, second, rho in correlations]
    return config, defaults


def profile(paths: list[Path], labels: list[str], options: privacy.Options) -> list[tuple[dict[str, Any], list[str]]]:
    """Profile CSV, TSV and Excel files into dataset configs.

    Args:
        paths: The files.
        labels: Generic label of each file, used as the output file name.
        options: The profile options.

    Returns:
        One dataset config per file, with the paths of its default fields.

    Raises:
        ValueError: If a file has no table with a header.
    """
    datasets = []
    read: list[tuple[Table, dict[str, Any], dict[str, Any]]] = []
    for path, label in zip(paths, labels, strict=True):
        tables = [kept_table(table, options) for table in read_tables(path) if table.header]
        if not tables:
            raise ValueError(f"{label}: no table with a header")
        workbook = privacy.file_suffix(path) in TYPED_SUFFIXES
        sheet_names = privacy.generic_names([table.sheet or "sheet_1" for table in tables], "sheet")
        if options.generic_labels:
            privacy.note_key(options, "sheet", label, None, "",
                             {table.sheet: sheet_names[table.sheet] for table in tables if table.sheet})
        dataset: dict[str, Any] = {"name": label.split(".", 1)[0], "format": format_of(path, tables[0]),
                                   "file": label}
        if dataset["format"] == "csv" and tables[0].delimiter not in (None, ","):
            dataset["delimiter"] = tables[0].delimiter
        sheets, defaults = [], []
        for index, table in enumerate(tables):
            real_name = table.sheet or "sheet_1"
            name = sheet_names[real_name] if options.generic_labels else real_name
            sheet, defaulted = sheet_config(path, label, table, name, options, workbook)
            sheets.append(sheet)
            read.append((table, dataset, sheet))
            defaults += [f"sheets[{index}].{item}" for item in defaulted]
        dataset["sheets"] = sheets
        datasets.append((dataset, defaults))
    links = key_links([(table, [column["kind"] for column in sheet["columns"]]) for table, _, sheet in read])
    for (table_index, index), (parent_table, parent_index) in links.items():
        _, parent_dataset, parent_sheet = read[parent_table]
        link = {"dataset": parent_dataset["name"], "column": parent_sheet["columns"][parent_index]["name"]}
        if len(parent_dataset["sheets"]) > 1:
            link["sheet"] = parent_sheet["name"]
        read[table_index][2]["columns"][index]["link"] = link
    return datasets


def audit_values(paths: list[Path], options: privacy.Options) -> dict[str, str]:
    """Collect the real values that must never appear in a config or a report.

    A standard missing token, such as ``#N/A``, holds no personal value and is
    not collected. A free text or label value that is part of a column name or
    of a category level that the report prints by rule 3 is not collected
    either: the TCGA-CDR workbook has the cancer type ``BRCA`` as a level of
    one sheet and as free text of another, and the free text ``Lung`` is part
    of the level ``Lung adenocarcinoma``. An identifier or a date is always
    collected, because a label that holds one is a leak.

    Args:
        paths: The real files.
        options: The options that made the config or the report.

    Returns:
        Each sensitive value mapped to its source kind.
    """
    found: dict[str, str] = {}
    public: set[str] = set()
    for path in paths:
        for table in read_tables(path):
            private = is_private_table(path, table, options)
            found.update(note_values([*table.preamble, *table.footer]))
            if options.generic_labels:
                found.update({name: "column name" for name in table.header})
                if table.sheet:
                    found[table.sheet] = "sheet name"
            else:
                public.update(str(name) for name in table.header)
            for index, name in enumerate(table.header):
                cells = table.column(index)
                stats = analyze_column(name, cells, options.max_categories)
                if stats.kind in AUDITED_KINDS:
                    found.update({value: AUDITED_KINDS[stats.kind] for value in stats.values})
                    found.update({cell.strftime("%Y-%m-%d"): "date" for cell in cells
                                  if isinstance(cell, (dt.date, dt.datetime))})
                elif stats.kind == "categorical" and (private or options.generic_labels):
                    found.update({value: "label" for value in stats.values})
                elif stats.kind == "categorical":
                    public.update(stats.values)
    public_text = "\n".join(public)
    return {value: kind for value, kind in found.items()
            if not privacy.is_missing_token(value)
            and (kind in ("identifier", "date") or value not in public_text)}


#: Significant digits of a number written with no ``decimals`` setting.
DEFAULT_SIGNIFICANT = 4

#: Marks each cell of an empty row, which stays empty whatever the na_token of its column.
EMPTY = object()


def identifier_cells(column: IdentifierColumn, count: int, rng: np.random.Generator) -> list[object]:
    """Fill an identifier column, in blocks when the identifiers repeat.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.

    Returns:
        The cells.
    """
    distinct = min(column.distinct or count, count) or 1
    sizes = dict(zip(column.patterns, pattern_counts(column.patterns, distinct), strict=True))
    identifiers: list[object] = []
    # Shorter shapes first, so the integers 1 to 9 come before 10 to 99.
    for pattern in sorted(sizes, key=len):
        identifiers += (invented_uuids(sizes[pattern], rng) if pattern == UUID_PATTERN
                        else invented_identifiers(pattern, sizes[pattern], rng))
    if column.as_number:
        identifiers = [int(value) for value in identifiers]
    return [identifiers[index * distinct // count] for index in range(count)]


def numeric_value(value: float, column: NumericColumn) -> float | int:
    """Round one number as the column writes it.

    Args:
        value: The drawn number.
        column: The column config.

    Returns:
        An int when the column has no decimals, else a rounded float.
    """
    if column.decimals == 0:
        return int(round(value))
    if column.decimals is not None:
        return round(value, column.decimals)
    return privacy.round_sig(value, DEFAULT_SIGNIFICANT)


def numeric_cells(column: NumericColumn, count: int, rng: np.random.Generator,
                  uniforms: np.ndarray | None = None) -> list[object]:
    """Fill a numeric column with numbers, exact values and tokens.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.
        uniforms: Draws between 0 and 1 from a copula, which keep a
            correlation with other columns. None draws them from ``rng``.

    Returns:
        The cells: numbers, and token texts where a token was drawn.
    """
    values = quantile_draws(column.quantiles, rng.random(count) if uniforms is None else uniforms)
    exact = list(column.exact)
    if exact:
        picks = pick_shares(list(column.exact.values()), count, rng)
        for index in np.flatnonzero(picks < len(exact)):
            values[index] = exact[picks[index]]
        if uniforms is not None:
            # The exact values land on random rows, so sort the values back into the
            # rank order of the copula draws. The values themselves do not change.
            values = np.sort(values)[np.argsort(np.argsort(uniforms, kind="stable"), kind="stable")]
    return apply_tokens([numeric_value(float(value), column) for value in values], column, rng)


def apply_tokens(cells: list[object], column: NumericColumn, rng: np.random.Generator) -> list[object]:
    """Put the non-numeric tokens of a column, such as ``NA``, and its empty cells in their shares.

    Tokens and empty cells are drawn together, so an empty cell never takes
    the place of a token.

    Args:
        cells: The cells, changed in place.
        column: The column config.
        rng: Random stream of the column.

    Returns:
        The same cells. An empty cell is None.
    """
    tokens = list(column.tokens)
    if tokens or column.missing:
        picks = pick_shares([*column.tokens.values(), column.missing], len(cells), rng)
        for index in np.flatnonzero(picks <= len(tokens)):
            cells[index] = tokens[picks[index]] if picks[index] < len(tokens) else None
    return cells


def apply_missing(cells: list[object], share: float, rng: np.random.Generator) -> list[object]:
    """Empty a share of the cells.

    Args:
        cells: The cells, changed in place.
        share: Share of the cells to empty.
        rng: Random stream of the column.

    Returns:
        The same cells.
    """
    if share:
        for index in np.flatnonzero(pick_shares([share], len(cells), rng) == 0):
            cells[index] = None
    return cells


def categorical_cells(column: CategoricalColumn, count: int, rng: np.random.Generator) -> list[object]:
    """Fill a categorical column with its levels in their shares.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.

    Returns:
        The cells.
    """
    levels = list(column.levels)
    shares = np.array([column.levels[level] for level in levels], dtype=float)
    return [levels[index] for index in pick_shares(list(shares / shares.sum()), count, rng)]


def text_cells(column: TextColumn, count: int, rng: np.random.Generator) -> list[object]:
    """Fill a free text column with invented words of the recorded lengths.

    A filled cell has one character or more, because an empty text is an empty
    cell, and the ``missing`` share alone sets the empty cells. With the old
    private default of 0 to 20 characters, 5% of the synthetic Titanic names
    were empty.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.

    Returns:
        The cells. A column with a provider gets the values of Faker.
    """
    if column.provider:
        return list(fake_values.provider_values(column.provider, column.locale, count, rng))
    shortest = max(column.lengths[0], min(1, column.lengths[1]))
    lengths = rng.integers(shortest, column.lengths[1] + 1, size=count)
    shapes = list(column.shapes)
    picks = pick_shares(list(column.shapes.values()), count, rng) if shapes else np.full(count, len(shapes))
    return [shaped_text(shapes[pick], rng) if pick < len(shapes) else invented_text(int(length), rng)
            for pick, length in zip(picks, lengths, strict=True)]


def date_cells(column: DateColumn, count: int, rng: np.random.Generator) -> list[object]:
    """Fill a date column: the anchor date plus drawn intervals.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.

    Returns:
        The cells as datetimes.
    """
    start = dt.datetime.combine(column.anchor, dt.time())
    days = quantile_draws(column.interval_days, rng.random(count))
    has_time = any(code in column.date_format for code in ("%H", "%M", "%S"))
    return [start + dt.timedelta(days=float(day) if has_time else round(float(day))) for day in days]


def boolean_cells(column: BooleanColumn, count: int, rng: np.random.Generator) -> list[object]:
    """Fill a boolean column in its true share.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.

    Returns:
        The cells as booleans.
    """
    return [bool(flag) for flag in rng.random(count) < column.true_share]


CELL_MAKERS = {"identifier": identifier_cells, "numeric": numeric_cells, "categorical": categorical_cells,
               "text": text_cells, "date": date_cells, "boolean": boolean_cells}


def column_cells(column: Any, count: int, rng: np.random.Generator) -> list[object]:
    """Fill one column, then empty its missing share.

    Args:
        column: The column config.
        count: Number of cells, empty rows excluded.
        rng: Random stream of the column.

    Returns:
        The cells. An empty cell is None.
    """
    cells = CELL_MAKERS[column.kind](column, count, rng)
    # A numeric column empties its cells with its tokens, in apply_tokens.
    return cells if column.kind == "numeric" else apply_missing(cells, column.missing, rng)


def text_cell(value: object, column: Any) -> str:
    """Write one cell as the text of a CSV or TSV file.

    Args:
        value: The cell.
        column: The column config.

    Returns:
        The text of the cell.
    """
    if value is EMPTY:
        return ""
    if value is None:
        return column.na_token or ""
    if isinstance(value, bool):
        return column.true_text if value else column.false_text
    if isinstance(value, dt.datetime):
        return value.strftime(column.date_format)
    suffix = getattr(column, "suffix", None) or ""
    if isinstance(value, float):
        if getattr(column, "decimals", None) is not None:
            return f"{value:.{column.decimals}f}{suffix}"
        return f"{value:g}{suffix}"
    if isinstance(value, int):
        return f"{value}{suffix}"
    return str(value)


def workbook_cell(value: object, column: Any) -> object:
    """Write one cell as a typed workbook cell.

    Args:
        value: The cell.
        column: The column config.

    Returns:
        The cell value for openpyxl.
    """
    if value is EMPTY:
        return None
    if value is None:
        return column.na_token
    if (getattr(column, "as_text", False) or getattr(column, "suffix", None)) and not isinstance(value, str):
        return text_cell(value, column)
    return value


def parquet_array(cells: list[object], column: Any) -> Any:
    """Build the typed Parquet column of one synthetic column.

    A missing or empty cell is a null. A date column is a date column when its
    ``date_format`` is ``%Y-%m-%d`` and a timestamp column otherwise. A column
    with ``as_text``, or with numbers beside tokens such as ``<LOD``, is a text
    column, as a real Parquet column with such cells is.

    Args:
        cells: The cells of the column.
        column: The column config.

    Returns:
        The ``pyarrow`` array.
    """
    import pyarrow as pa

    values = [None if cell is EMPTY else cell for cell in cells]
    if getattr(column, "as_text", False) or getattr(column, "suffix", None):
        return pa.array([None if value is None else text_cell(value, column) for value in values], pa.string())
    if column.kind == "date":
        if column.date_format == PARQUET_DATE:
            return pa.array([None if value is None else value.date() for value in values], pa.date32())
        return pa.array(values, pa.timestamp("us"))
    if all(value is None for value in values):
        return pa.array(values, pa.string())
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError):
        return pa.array([None if value is None else text_cell(value, column) for value in values], pa.string())


def rds_cells(cells: list[object], column: Any) -> list[object]:
    """Give the cells of one column the types of an R data frame column.

    pyreadr writes a date as text, so a date cell becomes its text in the
    format of the column. A column with ``as_text`` or a ``suffix``, and a
    column of numbers beside tokens such as ``<LOD``, holds text.

    Args:
        cells: The cells of the column.
        column: The column config.

    Returns:
        The cells: None, a bool, a number or a text.
    """
    values = [None if cell is EMPTY else cell for cell in cells]
    texts = getattr(column, "as_text", False) or getattr(column, "suffix", None) or any(
        isinstance(value, str) for value in values) and any(isinstance(value, (int, float)) for value in values)
    if column.kind == "date" or texts:
        return [None if value is None else text_cell(value, column) for value in values]
    return values


def column_keys(columns: list[Any]) -> list[str]:
    """Give each column a stream key, with a number on a repeated name.

    Args:
        columns: The column configs in order.

    Returns:
        One key per column: its name, or ``name#2`` for its second use.
    """
    seen: Counter = Counter()
    keys = []
    for column in columns:
        seen[column.name] += 1
        keys.append(column.name if seen[column.name] == 1 else f"{column.name}#{seen[column.name]}")
    return keys


def parent_keys(link: Link, config: Any) -> list[object]:
    """Draw again the keys of the column that a link names.

    The keys come from the random stream of that column, so they equal the
    cells that its own file holds.

    Args:
        link: The link.
        config: The whole config.

    Returns:
        The distinct keys, in file order, without the empty cells.
    """
    sheet, position, dataset = link_target(link, {item.name: item for item in config.datasets})
    count = sheet.rows - sheet.empty_rows
    key = column_keys(sheet.columns)[position]
    cells = column_cells(sheet.columns[position], count, stream(config.seed, dataset.name, sheet.name, key))
    return [cell for cell in dict.fromkeys(cells) if cell is not None]


def linked_cells(column: IdentifierColumn, count: int, rng: np.random.Generator, config: Any) -> list[object]:
    """Fill an identifier column from the keys of the column that its link names.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.
        config: The whole config.

    Returns:
        The cells: keys of the other table in blocks, and empty cells in the
        missing share.
    """
    keys = parent_keys(column.link, config)
    distinct = max(1, min(column.distinct or count, count, len(keys)))
    chosen = [keys[index] for index in sorted(rng.choice(len(keys), size=distinct, replace=False))]
    cells: list[object] = [chosen[index * distinct // count] for index in range(count)]
    cells = [int(cell) if column.as_number else str(cell) for cell in cells]
    return apply_missing(cells, column.missing, rng)


def table_cells(column: Any, count: int, rng: np.random.Generator, config: Any,
                uniforms: np.ndarray | None = None) -> list[object]:
    """Fill one column of a table, from the keys of another table for a linked identifier.

    Args:
        column: The column config.
        count: Number of cells.
        rng: Random stream of the column.
        config: The whole config, or None.
        uniforms: Copula draws of a correlated numeric column, or None.

    Returns:
        The cells.
    """
    if column.kind == "identifier" and column.link is not None and config is not None:
        return linked_cells(column, count, rng, config)
    if uniforms is not None:
        return numeric_cells(column, count, rng, uniforms)
    return column_cells(column, count, rng)


def copula_uniforms(sheet: Sheet, count: int, rng: np.random.Generator) -> dict[int, np.ndarray]:
    """Draw correlated uniforms for the numeric columns that the correlations of a sheet name.

    A Gaussian copula: a rank correlation ``rho`` becomes the normal correlation
    ``2 sin(pi rho / 6)``. A matrix that is not positive definite moves to the
    nearest one, with its eigenvalues raised to a small floor.

    Args:
        sheet: The sheet config.
        count: Number of rows.
        rng: Random stream of the correlations.

    Returns:
        Each correlated column position mapped to its uniforms.
    """
    if not sheet.correlations:
        return {}
    positions = {column.name: index for index, column in reversed(list(enumerate(sheet.columns)))}
    involved = sorted({positions[name] for correlation in sheet.correlations for name in correlation.columns})
    place = {position: order for order, position in enumerate(involved)}
    matrix = np.eye(len(involved))
    for correlation in sheet.correlations:
        first, second = (place[positions[name]] for name in correlation.columns)
        matrix[first, second] = matrix[second, first] = 2 * np.sin(np.pi * correlation.rho / 6)
    values, vectors = np.linalg.eigh(matrix)
    matrix = vectors @ np.diag(np.clip(values, 1e-6, None)) @ vectors.T
    scale = np.sqrt(np.diag(matrix))
    matrix = matrix / np.outer(scale, scale)
    normals = rng.standard_normal((count, len(involved))) @ np.linalg.cholesky(matrix).T
    uniforms = special.ndtr(normals)
    return {position: uniforms[:, place[position]] for position in involved}


def sheet_rows(dataset: TabularDataset, sheet: Sheet, seed: int, config: Any = None) -> list[list[object]]:
    """Fill one sheet in the resample mode: each column on its own.

    Args:
        dataset: The dataset config.
        sheet: The sheet config.
        seed: The config seed.
        config: The whole config, which a linked identifier needs.

    Returns:
        The rows, with the empty rows at drawn positions.
    """
    count = sheet.rows - sheet.empty_rows
    keys = column_keys(sheet.columns)
    uniforms = copula_uniforms(sheet, count, stream(seed, dataset.name, sheet.name, "correlations"))
    columns = [table_cells(column, count, stream(seed, dataset.name, sheet.name, key), config, uniforms.get(index))
               for index, (column, key) in enumerate(zip(sheet.columns, keys, strict=True))]
    apply_date_order(sheet, columns)
    rows: list[list[object]] = [list(row) for row in zip(*columns, strict=True)] if columns else []
    return insert_empty_rows(rows, sheet, stream(seed, dataset.name, sheet.name, "empty_rows"))


def apply_date_order(sheet: Sheet, columns: list[list[object]]) -> None:
    """Move each date of a column with ``after`` to the date of the earlier column plus its drawn interval.

    The drawn interval is the distance of the cell from the anchor, so the
    column keeps its own draws and its missing cells.

    Args:
        sheet: The sheet config.
        columns: The cells of each column, changed in place.
    """
    for index, column in enumerate(sheet.columns):
        if column.kind != "date" or column.after is None:
            continue
        reference = next(position for position, item in enumerate(sheet.columns[:index])
                         if item.name == column.after)
        start = dt.datetime.combine(column.anchor, dt.time())
        for row, (cell, earlier) in enumerate(zip(columns[index], columns[reference], strict=True)):
            if isinstance(cell, dt.datetime) and isinstance(earlier, dt.datetime):
                columns[index][row] = earlier + (cell - start)


def insert_empty_rows(rows: list[list[object]], sheet: Sheet, rng: np.random.Generator) -> list[list[object]]:
    """Insert the empty rows of a sheet at drawn positions, never at the end.

    Args:
        rows: The filled rows, changed in place.
        sheet: The sheet config.
        rng: Random stream of the empty rows.

    Returns:
        The same rows, with the empty rows in place.
    """
    total = len(rows) + sheet.empty_rows
    # A reader drops empty rows at the end of a table, so the last row holds content.
    limit = total - 1 if rows else total
    for position in sorted(rng.choice(limit, size=sheet.empty_rows, replace=False)):
        rows.insert(int(position), [EMPTY] * len(sheet.columns))
    return rows


def design_cell(row: DesignRow, field: str) -> object:
    """Return the value of a design column for one design row.

    Args:
        row: The design row.
        field: ``subject``, ``group``, ``timepoint``, ``sample`` or ``replicate``.

    Returns:
        The cell.
    """
    sample = row.sample
    return {"subject": sample.subject, "group": sample.group, "timepoint": sample.timepoint,
            "sample": sample.sample_id, "replicate": row.replicate}[field]


def model_value(value: float, censored: bool, limit: float, decimals: int | None,
                censor_token: str | None) -> object:
    """Write one simulated value, or its censored form.

    Args:
        value: The simulated value.
        censored: The value is below the lower limit.
        limit: The lower limit of the feature.
        decimals: Decimal places, or None for four significant digits.
        censor_token: Text of a censored value, or None to write the limit.

    Returns:
        The cell.
    """
    if censored and censor_token is not None:
        return censor_token
    number_ = limit if censored else value
    if decimals == 0:
        return int(round(number_))
    return round(number_, decimals) if decimals is not None else privacy.round_sig(number_, DEFAULT_SIGNIFICANT)


def sheet_features(sheet: Sheet) -> list[Feature]:
    """Return the simulated features of a design sheet.

    Args:
        sheet: The sheet config.

    Returns:
        The features of a long sheet, or one per numeric column of a wide one.
    """
    if sheet.layout == "long":
        return [Feature(spec.name, spec.quantiles, spec.scale) for spec in sheet.features]
    return [Feature(column.name, column.quantiles, column.scale)
            for column in sheet.columns if column.kind == "numeric"]


def model_sheet_rows(dataset: TabularDataset, sheet: Sheet, context: Context) -> list[list[object]]:
    """Fill one design sheet in the model mode.

    Design columns take the design rows, the features take the simulated
    values, and every other column draws on its own as in the resample mode.

    Args:
        dataset: The dataset config.
        sheet: The sheet config.
        context: The generation context, which collects the truth rows.

    Returns:
        The rows, with the empty rows at drawn positions.
    """
    config = context.config
    features = sheet_features(sheet)
    simulation = simulate(config, dataset.name, features, key=f"{dataset.name}/{sheet.name}")
    context.add_truth("effects", simulation.effects)
    context.add_truth("censored", simulation.censored_truth)
    token = config.artefacts.censor_token if config.artefacts else None
    decimals = ([spec.decimals for spec in sheet.features] if sheet.layout == "long"
                else [column.decimals for column in sheet.columns if column.kind == "numeric"])
    pairs = [(row_index, feature_index) for row_index in range(len(simulation.rows))
             for feature_index in (range(len(features)) if sheet.layout == "long" else [0])]
    keys = column_keys(sheet.columns)
    columns = []
    numeric_index = 0
    for column, key in zip(sheet.columns, keys, strict=True):
        rng = stream(config.seed, dataset.name, sheet.name, key)
        if column.kind == "design" and column.field == "feature":
            cells = [features[feature_index].name for _, feature_index in pairs]
        elif column.kind == "design" and column.field == "value":
            cells = [model_value(simulation.values[row, feature], simulation.censored[row, feature],
                                 simulation.limits[feature], decimals[feature], token) for row, feature in pairs]
            if dataset.format != "excel":
                cells = [f"{cell:.{decimals[feature]}f}" if isinstance(cell, float) and decimals[feature] else cell
                         for cell, (_, feature) in zip(cells, pairs, strict=True)]
            cells = apply_missing(cells, column.missing, rng)
        elif column.kind == "design":
            cells = [design_cell(simulation.rows[row], column.field) for row, _ in pairs]
        elif column.kind == "numeric" and sheet.layout == "wide":
            cells = [model_value(simulation.values[row, numeric_index], simulation.censored[row, numeric_index],
                                 simulation.limits[numeric_index], column.decimals, token)
                     for row in range(len(simulation.rows))]
            cells = apply_tokens(cells, column, rng)
            numeric_index += 1
        else:
            cells = table_cells(column, len(pairs), rng, config)
        columns.append(cells)
    apply_date_order(sheet, columns)
    rows = [list(row) for row in zip(*columns, strict=True)]
    return insert_empty_rows(rows, sheet, stream(config.seed, dataset.name, sheet.name, "empty_rows"))


def write_dataset(dataset: TabularDataset, sheets: list[tuple[Sheet, list[list[object]]]], path: Path) -> None:
    """Write the filled sheets of a dataset in its format.

    Args:
        dataset: The dataset config.
        sheets: Each sheet config with its rows.
        path: The output file.
    """
    if dataset.format == "rds":
        sheet, rows = sheets[0]
        cells = [list(column) for column in zip(*rows, strict=True)] if rows else [[] for _ in sheet.columns]
        write_rds(path, [column.name for column in sheet.columns],
                  [rds_cells(column_values, column) for column_values, column in zip(cells, sheet.columns,
                                                                                    strict=True)])
        return
    if dataset.format == "parquet":
        sheet, rows = sheets[0]
        cells = [list(column) for column in zip(*rows, strict=True)] if rows else [[] for _ in sheet.columns]
        write_parquet(path, [column.name for column in sheet.columns],
                      [parquet_array(column_values, column)
                       for column_values, column in zip(cells, sheet.columns, strict=True)])
        return
    if dataset.format == "excel":
        write_workbook(path, [Table(sheet.name, [column.name for column in sheet.columns],
                                    [[workbook_cell(value, column)
                                      for value, column in zip(row, sheet.columns, strict=True)] for row in rows],
                                    preamble=note_rows(sheet.preamble), footer=note_rows(sheet.footer))
                              for sheet, rows in sheets])
        return
    sheet, rows = sheets[0]
    delimiter = dataset.delimiter or ("\t" if dataset.format == "tsv" else ",")
    write_text_table(path, [column.name for column in sheet.columns],
                     [[text_cell(value, column) for value, column in zip(row, sheet.columns, strict=True)]
                      for row in rows], delimiter, note_rows(sheet.preamble), note_rows(sheet.footer))


def note_rows(notes: list[NoteRow]) -> list[list[object]]:
    """Build the rows above the header or below the data of a synthetic file.

    Args:
        notes: The configured rows.

    Returns:
        One row of ``note`` cells per configured row.
    """
    return [note_cells(note.cells, note.start, note.mark) for note in notes]


def generate(dataset: TabularDataset, context: Context) -> list[Path]:
    """Write one tabular dataset.

    Args:
        dataset: The dataset config.
        context: The generation context.

    Returns:
        The file written.
    """
    config = context.config
    sheets = [(sheet, model_sheet_rows(dataset, sheet, context) if config.mode == "model" and sheet.design_fields
               else sheet_rows(dataset, sheet, config.seed, config)) for sheet in dataset.sheets]
    path = context.out_dir / dataset.file
    write_dataset(dataset, sheets, path)
    return [path]


#: Template of a tabular dataset for ``sdg init``, as a YAML list item.
TEMPLATE = '''\
  # name: the name that effects use; format: csv, tsv, excel, parquet or rds;
  # file: the output file, relative to the output folder
  - name: {name}
    format: {format}
    file: {file}
    # delimiter: ";"                # csv or tsv only; default comma for csv, tab for tsv
    sheets:
      - name: sheet_1               # workbook sheet name; a text file ignores it
        rows: 100                   # data rows, empty rows included
        empty_rows: 0               # rows with every cell empty
        columns:                    # in file order; a name can repeat
          - name: subject_id
            kind: identifier        # shape: A is a letter, 9 is a digit
            pattern: "SUBJ-999"
            distinct: 20            # values repeat in blocks; omit for unique values
          - name: group
            kind: categorical
            levels: {{group_a: 0.5, group_b: 0.5}}   # each level and its share; shares sum to 1
          - name: value
            kind: numeric
            quantiles: [0.5, 1.0, 2.0, 4.0, 8.0]  # evenly spaced from the minimum to the maximum
            missing: 0.05           # share of empty cells
            tokens: {{NA: 0.02}}      # non-numeric texts and their shares
            decimals: 2
          - name: note
            kind: text              # free text: invented words of these lengths
            lengths: [1, 30]
            # shapes: {{A99: 0.6}}    # common shapes and their shares of the filled cells
          - name: visit_date
            kind: date              # anchor date (2000-01-01) plus drawn intervals
            interval_days: [0, 30, 60]
            date_format: "%Y-%m-%d"
          - name: flag
            kind: boolean
            true_share: 0.5
'''


#: Template of a tabular design dataset for ``sdg init --mode model``.
MODEL_TEMPLATE = '''\
  # name: the name that effects use; format: csv, tsv, excel, parquet or rds;
  # file: the output file, relative to the output folder
  - name: {name}
    format: {format}
    file: {file}
    sheets:
      - name: sheet_1
        # wide: one row per sample, each numeric column a feature.
        # long: one row per sample and feature; add design columns of field
        # feature and value, and a features list with name and quantiles.
        layout: wide
        empty_rows: 0
        columns:
          - name: subject_id
            kind: design            # filled from the design
            field: subject          # subject, group, timepoint, sample, replicate, feature or value
          - name: group
            kind: design
            field: group
          - name: visit
            kind: design
            field: timepoint
          - name: value             # a feature: the model draws it, and effects move it
            kind: numeric
            quantiles: [0.5, 1.0, 2.0, 4.0, 8.0]
            scale: linear           # linear: an effect multiplies; log2: an effect adds log2(fold change)
            decimals: 2
          - name: note              # other kinds draw on their own, as in the resample mode
            kind: text
            lengths: [1, 20]
'''


def template(fmt: str, mode: str, name: str) -> tuple[str, str | None]:
    """Return the commented template of one tabular dataset for ``sdg init``.

    Args:
        fmt: ``csv``, ``tsv``, ``excel`` or ``parquet``.
        mode: ``resample`` or ``model``.
        name: Dataset name.

    Returns:
        The YAML list item, and the feature that a template effect can target.
    """
    suffix = {"csv": ".csv", "tsv": ".tsv", "excel": ".xlsx", "parquet": ".parquet", "rds": ".rds"}[fmt]
    chosen = MODEL_TEMPLATE if mode == "model" else TEMPLATE
    return chosen.format(name=name, format=fmt, file=f"{name}{suffix}"), "value" if mode == "model" else None


def output_tables(dataset: TabularDataset, out_dir: Path) -> dict[str, Table]:
    """Read the output file of a dataset, by sheet name.

    Args:
        dataset: The dataset config.
        out_dir: The output folder.

    Returns:
        Each table by sheet name. A text file has the name of its one sheet.
    """
    path = out_dir / dataset.file
    delimiter = dataset.delimiter or ("\t" if dataset.format == "tsv" else None)
    tables = read_tables(path, delimiter)
    if dataset.format != "excel":
        return {dataset.sheets[0].name: tables[0]}
    return {table.sheet: table for table in tables}


def expected_rows(dataset: TabularDataset, sheet: Sheet, config: Any) -> int:
    """Return the number of data rows that a sheet must have.

    Args:
        dataset: The dataset config.
        sheet: The sheet config.
        config: The whole config.

    Returns:
        The configured rows, or for a design sheet the measured rows (times
        the features of a long sheet) plus the empty rows.
    """
    if not (config.mode == "model" and sheet.design_fields):
        return sheet.rows
    per_row = len(sheet.features) if sheet.layout == "long" else 1
    return measured_rows(config, dataset.name) * per_row + sheet.empty_rows


def check_output(dataset: TabularDataset, config: Any, out_dir: Path) -> list[str]:
    """Compare the output file with the config: sheets, headers and row counts.

    Args:
        dataset: The dataset config.
        config: The whole config.
        out_dir: The output folder.

    Returns:
        The problems found.
    """
    if not (out_dir / dataset.file).is_file():
        return [f"{dataset.file}: missing"]
    tables = output_tables(dataset, out_dir)
    issues = []
    for sheet in dataset.sheets:
        table = tables.get(sheet.name)
        where = f"{dataset.file} | {sheet.name}"
        if table is None:
            issues.append(f"{where}: sheet missing")
            continue
        header = [column.name for column in sheet.columns]
        if table.header != header:
            issues.append(f"{where}: header {table.header} differs from the config {header}")
        if len(table.preamble) != len(sheet.preamble):
            issues.append(f"{where}: the header is on row {len(table.preamble) + 1}, the config gives "
                          f"{len(sheet.preamble) + 1}")
        if len(table.footer) != len(sheet.footer):
            issues.append(f"{where}: {len(table.footer)} footer rows, the config gives {len(sheet.footer)}")
        if len(table.rows) != expected_rows(dataset, sheet, config):
            issues.append(f"{where}: {len(table.rows)} rows, the config gives {expected_rows(dataset, sheet, config)}")
    return issues


def row_sample(row: list[object], positions: dict[str, int]) -> str | None:
    """Return the sample identifier of a design row as read back.

    Args:
        row: The row.
        positions: Position of each design field.

    Returns:
        The sample column, else the subject with the timepoint, or None.
    """
    if "sample" in positions:
        return str(row[positions["sample"]])
    if "subject" not in positions or is_empty(row[positions["subject"]]):
        return None
    timepoint = row[positions["timepoint"]] if "timepoint" in positions else None
    subject = str(row[positions["subject"]])
    return subject if is_empty(timepoint) else f"{subject}_{timepoint}"


def observed(dataset: TabularDataset, out_dir: Path) -> dict[tuple[str, str], list[float]]:
    """Read the feature values of the design sheets back, by sample and feature.

    Args:
        dataset: The dataset config.
        out_dir: The output folder.

    Returns:
        The numeric values of each sample and feature. A token, such as a
        censored value, is left out.
    """
    tables = output_tables(dataset, out_dir)
    found: dict[tuple[str, str], list[float]] = {}
    for sheet in dataset.sheets:
        table = tables.get(sheet.name)
        if table is None or not sheet.design_fields:
            continue
        positions = {column.field: index for index, column in enumerate(sheet.columns) if column.kind == "design"}
        numeric = [(index, column.name) for index, column in enumerate(sheet.columns) if column.kind == "numeric"]
        for row in table.rows:
            sample = row_sample(row, positions)
            if sample is None:
                continue
            pairs = ([(str(row[positions["feature"]]), row[positions["value"]])] if sheet.layout == "long"
                     else [(name, row[index]) for index, name in numeric])
            for feature, cell in pairs:
                value = number(cell)
                if value is not None:
                    found.setdefault((sample, feature), []).append(value)
    return found
