"""Feature by sample matrices: a count matrix or a microarray expression matrix.

A matrix file has one row per feature. Its first column holds the feature
identifiers; other text columns, such as a gene symbol, are annotation columns;
the numeric columns are the samples.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import Field, field_validator

from sdg import privacy
from sdg.config_base import StrictModel, check_non_decreasing
from sdg.modes.resample import quantile_draws
from sdg.report import ColumnReport, Fact, TableReport, counted
from sdg.tables import (analyze_column, column_report, layout_facts, note_cells, note_fields, number,
                        read_text_table, rounded_quantiles, write_text_table)

#: A column is a sample column when at least this share of its cells are numbers.
SAMPLE_SHARE = 0.9

#: A sample name with a replicate suffix, as ``sample_names`` writes it.
REPLICATE_SUFFIX = re.compile(r"_rep\d+$")

#: A feature name that ends in its number, such as ``gene_1``.
NUMBERED = re.compile(r"^(?P<prefix>.*?)1$")

#: A numeric column with one of these names describes the feature, so it is an
#: annotation column and not a sample. featureCounts writes ``Chr``, ``Start``,
#: ``End``, ``Strand`` and ``Length``, and the GEO count table of GSE60450 has a
#: ``Length`` column, which read as a 13th sample.
ANNOTATION_NAMES = re.compile(r"^(chr|chrom|chromosome|start|end|strand|length|effective_?length|gene_?length)$",
                              re.IGNORECASE)


class NumericAnnotation(StrictModel):
    """A numeric annotation column of a matrix, such as the gene length.

    Code that divides the counts by the gene length needs a number in each
    cell, so this column does not repeat the feature identifier as a text
    annotation column does.

    Attributes:
        name: Column header.
        quantiles: Values at evenly spaced probabilities from 0 to 1.
        decimals: Decimal places of a written value.
    """

    name: str
    quantiles: list[float] = Field(min_length=2)
    decimals: int = Field(default=0, ge=0, le=12)

    check_quantiles = field_validator("quantiles")(check_non_decreasing)


#: An annotation column of a matrix config: the header of a text column, which
#: repeats the feature identifier, or a numeric column.
Annotation = str | NumericAnnotation

#: Quantiles of a numeric annotation column with too few numbers to profile.
DEFAULT_ANNOTATION_QUANTILES = [0.0, 100.0]


@dataclass
class Matrix:
    """A matrix file as read.

    Attributes:
        feature_column: Header of the feature identifier column.
        features: Feature identifiers, one per row.
        annotation_columns: Headers of the annotation columns.
        annotations: Cells of each annotation column.
        samples: Sample column headers.
        values: Values, features by samples. A cell that is not a number is NaN.
        delimiter: Delimiter of the file.
        order: Header of each column in file order.
        preamble: Rows above the header, such as the metadata lines of a GEO
            series matrix or the command line of featureCounts.
        footer: Comment rows below the data.
    """

    feature_column: str
    features: list[str]
    annotation_columns: list[str]
    annotations: list[list[str]]
    samples: list[str]
    values: np.ndarray
    delimiter: str
    order: list[str]
    preamble: list[list[object]] = field(default_factory=list)
    footer: list[list[object]] = field(default_factory=list)


def read_matrix(path: Path) -> Matrix:
    """Read a matrix file.

    Args:
        path: A CSV, TSV or TXT file with features in rows.

    Returns:
        The matrix.

    Raises:
        ValueError: If the file has no sample column.
    """
    table = read_text_table(path)
    features = [str(row[0]) for row in table.rows]
    annotation_columns, annotations, samples, columns = [], [], [], []
    for index, name in enumerate(table.header[1:], start=1):
        cells = table.column(index)
        parsed = [number(cell) for cell in cells]
        if (not ANNOTATION_NAMES.match(name.strip())
                and sum(value is not None for value in parsed) >= SAMPLE_SHARE * max(len(cells), 1)):
            samples.append(name)
            columns.append([np.nan if value is None else value for value in parsed])
        else:
            annotation_columns.append(name)
            annotations.append([str(cell) for cell in cells])
    if not samples:
        raise ValueError(f"{path.name}: no numeric sample column")
    values = np.array(columns, dtype=float).T if columns else np.empty((len(features), 0))
    return Matrix(table.header[0] if table.header else "", features, annotation_columns, annotations, samples,
                  values, table.delimiter or ",", table.header, table.preamble, table.footer)


def write_matrix(path: Path, feature_column: str, features: list[str], annotation_columns: list[str],
                 annotations: list[list[str]], samples: list[str], values: np.ndarray, delimiter: str,
                 decimals: int | None, notes: tuple[list[Any], list[Any]] = ([], [])) -> None:
    """Write a matrix file with features in rows.

    Args:
        path: The output file.
        feature_column: Header of the feature identifier column.
        features: Feature identifiers.
        annotation_columns: Headers of the annotation columns.
        annotations: Cells of each annotation column, from :func:`annotation_cells`.
        samples: Sample column headers.
        values: Values, features by samples.
        delimiter: The delimiter.
        decimals: Decimal places, 0 for counts, or None for four significant digits.
        notes: The configured rows above the header and below the data.
    """
    if decimals == 0:
        text = values.astype(np.int64).astype(str)
    elif decimals is None:
        text = np.char.mod("%.4g", values)
    else:
        text = np.char.mod(f"%.{decimals}f", values)
    rows = [[feature, *(cells[index] for cells in annotations), *text[index]]
            for index, feature in enumerate(features)]
    preamble, footer = ([note_cells(note.cells, note.start, note.mark) for note in group] for group in notes)
    write_text_table(path, [feature_column, *annotation_columns, *samples], rows, delimiter, preamble, footer)


def sample_report(samples: list[str], limit: int) -> ColumnReport:
    """Report the sample columns of a matrix as one column: their name shapes.

    Args:
        samples: Sample column headers.
        limit: Most shapes to list.

    Returns:
        The column report.
    """
    shapes = Counter(privacy.shape(sample) for sample in samples)
    return ColumnReport("<samples>", "samples", [Fact("count", str(len(samples))),
                                                Fact("shapes", counted(shapes, limit))])


def matrix_report(label: str, fmt: str, matrix: Matrix, options: privacy.Options,
                  extra: list[Fact]) -> TableReport:
    """Report a matrix under the privacy rules.

    Args:
        label: Generic file label.
        fmt: Format name, ``counts`` or ``microarray``.
        matrix: The matrix.
        options: The glimpse options.
        extra: Format facts, such as the zero share of a count matrix.

    Returns:
        The table report: the feature column, the annotation columns and one
        column that stands for all sample columns.
    """
    facts = [Fact("features", str(len(matrix.features))), Fact("samples", str(len(matrix.samples))),
             Fact("delimiter", repr(matrix.delimiter)), *layout_facts(matrix.preamble, matrix.footer),
             Fact("orientation", "features by samples"), *extra]
    names = privacy.generic_names([matrix.feature_column, *matrix.annotation_columns], "column") \
        if options.generic_labels else {}
    report = TableReport(label, fmt, facts)
    stats = analyze_column(matrix.feature_column, matrix.features, options.max_categories)
    report.columns.append(column_report(stats, False, options.max_categories, names.get(matrix.feature_column)))
    for name, cells in zip(matrix.annotation_columns, matrix.annotations, strict=True):
        stats = analyze_column(name, cells, options.max_categories)
        report.columns.append(column_report(stats, False, options.max_categories, names.get(name)))
    report.columns.append(sample_report(matrix.samples, options.max_categories))
    return report


def annotation_names(columns: list[Annotation]) -> list[str]:
    """Return the headers of the configured annotation columns.

    Args:
        columns: The annotation columns of a matrix config.

    Returns:
        The header of each column.
    """
    return [column if isinstance(column, str) else column.name for column in columns]


def annotation_cells(columns: list[Annotation], features: list[str], rng: np.random.Generator) -> list[list[str]]:
    """Fill the annotation columns of a synthetic matrix.

    A text column repeats the feature identifier, so code that reads it finds
    a text value. A numeric column draws from its quantiles.

    Args:
        columns: The annotation columns of a matrix config.
        features: Feature identifiers.
        rng: Random generator of the numeric columns.

    Returns:
        The cells of each column, one per feature.
    """
    cells = []
    for column in columns:
        if isinstance(column, str):
            cells.append(list(features))
        else:
            drawn = quantile_draws(column.quantiles, rng.random(len(features)))
            cells.append([f"{value:.{column.decimals}f}" for value in drawn])
    return cells


def annotation_fields(matrix: Matrix, options: privacy.Options) -> tuple[dict[str, Any], list[str]]:
    """Record the annotation columns of a matrix.

    A text column records its header. A numeric column, such as a gene
    length, records its rounded deciles and its decimal places, under the
    rules of a numeric table column.

    Args:
        matrix: The matrix.
        options: The profile options.

    Returns:
        The ``annotation_columns`` field when the matrix has one, and the
        paths of the fields that hold defaults.
    """
    columns: list[Any] = []
    defaults = []
    for index, (name, cells) in enumerate(zip(matrix.annotation_columns, matrix.annotations, strict=True)):
        label = f"annotation_{index + 1}" if options.generic_labels else name
        stats = analyze_column(name, cells, options.max_categories)
        if stats.kind != "numeric":
            columns.append(label)
        elif len(stats.numbers) >= privacy.MIN_VALUES:
            columns.append({"name": label, "quantiles": rounded_quantiles(stats.numbers),
                            "decimals": stats.decimals or 0})
        else:
            columns.append({"name": label, "quantiles": list(DEFAULT_ANNOTATION_QUANTILES)})
            defaults.append(f"annotation_columns[{index}].quantiles")
    return ({"annotation_columns": columns} if columns else {}), defaults


def notes_fields(matrix: Matrix) -> dict[str, Any]:
    """Record the rows above the header and below the data of a matrix.

    Args:
        matrix: The matrix.

    Returns:
        The ``preamble`` and ``footer`` config fields that apply.
    """
    fields: dict[str, Any] = {}
    if matrix.preamble:
        fields["preamble"] = [note_fields(row) for row in matrix.preamble]
    if matrix.footer:
        fields["footer"] = [note_fields(row) for row in matrix.footer]
    return fields


def notes_issues(name: str, matrix: Matrix, dataset: Any) -> list[str]:
    """Check the rows around an output matrix against its config.

    Args:
        name: The file name, for the message.
        matrix: The output matrix as read.
        dataset: The dataset config, with ``preamble`` and ``footer``.

    Returns:
        The problems found.
    """
    issues = []
    if len(matrix.preamble) != len(dataset.preamble):
        issues.append(f"{name}: the header is on row {len(matrix.preamble) + 1}, the config gives "
                      f"{len(dataset.preamble) + 1}")
    if len(matrix.footer) != len(dataset.footer):
        issues.append(f"{name}: {len(matrix.footer)} footer rows, the config gives {len(dataset.footer)}")
    return issues


def generated_features(features: list[str], count: int | None, prefix: str) -> list[str]:
    """Return the feature names of a matrix dataset.

    Args:
        features: The listed features, or an empty list.
        count: Feature count when the list is empty.
        prefix: Prefix of a generated name.

    Returns:
        The listed features, or ``<prefix><n>`` for each of ``count`` features.
    """
    return list(features) if features else [f"{prefix}{index}" for index in range(1, (count or 0) + 1)]


def feature_fields(features: list[str], options: privacy.Options) -> dict[str, Any]:
    """Record the feature names, or only their count with generic labels.

    Args:
        features: The real feature names.
        options: The profile options.

    Returns:
        ``features``, or ``feature_count`` with generic labels.
    """
    if options.generic_labels:
        return {"feature_count": len(features)}
    match = NUMBERED.match(features[0]) if features else None
    if match and features == generated_features([], len(features), match["prefix"]):
        return {"feature_count": len(features), "feature_prefix": match["prefix"]}
    return {"features": list(features)}


def most_common_shape(values: list[str]) -> str:
    """Return the most common shape of a list of names (privacy rule 1).

    Args:
        values: The names.

    Returns:
        The most common shape.
    """
    return Counter(privacy.shape(value) for value in values).most_common(1)[0][0]


def sample_names(row_ids: list[tuple[str, int]], replicates: int) -> list[str]:
    """Name the sample columns of a model-mode matrix.

    Args:
        row_ids: Each design row as its sample identifier and replicate number.
        replicates: Replicates of each sample.

    Returns:
        The sample identifier, with ``_rep<n>`` when there are replicates.
    """
    return [sample if replicates == 1 else f"{sample}_rep{replicate}" for sample, replicate in row_ids]


def strip_replicate(name: str) -> str:
    """Remove the replicate suffix of a sample name.

    Args:
        name: A sample name such as ``SUBJ-001_t1_rep2``.

    Returns:
        The sample identifier.
    """
    return REPLICATE_SUFFIX.sub("", name)
