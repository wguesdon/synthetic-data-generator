"""Count matrices (bulk RNA-seq) and AnnData files (single-cell counts).

Both formats draw counts from a negative binomial distribution. The config
holds no per-gene value: it holds the quantiles of the gene means across genes
and a dispersion trend, ``dispersion + dispersion_extra / mean``, the
parametric trend of DESeq2. Gene identifiers are public names, so they stay
unless ``--generic-labels`` is set.

A count matrix in the model mode has one column per design sample, and an
effect multiplies the mean of a gene in the moved group. An AnnData file is
resample only; a cluster column gives the cells marker genes, so clustering
code finds groups.

``anndata`` loads inside the AnnData functions only, because its import is
slow and most commands never need it.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
import scipy.sparse as sparse
from pydantic import Field, field_validator, model_validator

from sdg import privacy
from sdg.config_base import DatasetBase, NoteRow, StrictModel
from sdg.formats import tabular
from sdg.matrix import (Annotation, annotation_cells, annotation_fields, annotation_names, feature_fields,
                        generated_features, matrix_report, most_common_shape, notes_fields, notes_issues, read_matrix,
                        sample_names, strip_replicate, write_matrix)
from sdg.modes.model import Feature, lognormal_quantiles, replicate_count, sample_count, simulate
from sdg.modes.resample import invented_identifiers, quantile_draws
from sdg.report import Fact, TableReport
from sdg.seeding import stream
from sdg.tables import (ColumnStats, analyze_column, column_report, generic_columns, level_names, note_columns,
                        note_values)
from sdg.version import SDG_VERSION

if TYPE_CHECKING:
    from sdg.generate import Context

FORMATS = ("counts", "h5ad")

#: Suffixes that the detection claims. A count matrix in CSV or TSV form needs
#: ``--format counts``, because a plain table has the same suffix.
SUFFIXES = (".h5ad",)

#: Most cells that a profile reads from an AnnData file.
PROFILE_CELLS = 1000

#: Cells written per block, so a large AnnData file never sits dense in memory.
CELL_BLOCK = 500

#: Largest dispersion of a gene, for a gene with a mean near zero.
MAX_DISPERSION = 10.0

#: An obs column with one of these names holds cluster labels.
CLUSTER_NAMES = re.compile(r"leiden|louvain|cluster|cell_?type|annotation", re.IGNORECASE)

#: Defaults where the privacy rules forbid a measurement.
DEFAULT_LOG_MEANS = [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.5, 9.0, 13.0]
DEFAULT_DISPERSION = 0.05
DEFAULT_DISPERSION_EXTRA = 1.0


class NegativeBinomialFields(StrictModel):
    """The count model that the two formats share.

    Attributes:
        features: Feature identifiers in order. Empty generates ``feature_count``
            names from ``feature_prefix``.
        feature_count: Number of features when ``features`` is empty.
        feature_prefix: Prefix of a generated feature name.
        log_mean_quantiles: Quantiles of log2(mean count + 1) across features.
        dispersion: Asymptotic dispersion of the trend.
        dispersion_extra: Extra-Poisson part of the trend, divided by the mean.
        library_size_cv: Coefficient of variation of the library sizes.
    """

    features: list[str] = Field(default_factory=list)
    feature_count: int | None = Field(default=None, ge=1)
    feature_prefix: str = "gene_"
    log_mean_quantiles: list[float] = Field(min_length=2)
    dispersion: float = Field(default=DEFAULT_DISPERSION, ge=0.0)
    dispersion_extra: float = Field(default=DEFAULT_DISPERSION_EXTRA, ge=0.0)
    library_size_cv: float = Field(default=0.2, ge=0.0)

    check_quantiles = field_validator("log_mean_quantiles")(tabular.check_non_decreasing)

    @model_validator(mode="after")
    def check_feature_source(self) -> NegativeBinomialFields:
        """Check that the features come from a list or a count.

        Returns:
            The unchanged model.

        Raises:
            ValueError: If both or neither are set.
        """
        if bool(self.features) == (self.feature_count is not None):
            raise ValueError("features: give a feature list or a feature_count, not both")
        return self

    def feature_list(self) -> list[str]:
        """Return the feature names in order.

        Returns:
            The listed features, or the generated ones.
        """
        return generated_features(self.features, self.feature_count, self.feature_prefix)


class CountMatrixDataset(NegativeBinomialFields, DatasetBase):
    """A bulk count matrix: genes in rows, samples in columns.

    Attributes:
        format: ``counts``.
        delimiter: Delimiter. ``None`` uses a tab for ``.tsv`` and ``.txt``, else a comma.
        feature_column: Header of the gene identifier column.
        annotation_columns: Columns after the identifier: the header of a text
            column, such as a gene symbol, which repeats the identifier, or a
            numeric column, such as the gene length, with its quantiles.
        samples: Sample count in the resample mode. The model mode takes the
            samples of the design.
        sample_pattern: Shape of a sample column header in the resample mode.
        biological_cv: Coefficient of variation of a gene mean between samples.
        preamble: Rows above the header, such as the command line that
            featureCounts writes.
        footer: Comment rows below the data.
    """

    format: Literal["counts"]
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)
    feature_column: str = "gene_id"
    annotation_columns: list[Annotation] = Field(default_factory=list)
    samples: int | None = Field(default=None, ge=1)
    sample_pattern: str = "SMPL-99"
    biological_cv: float = Field(default=0.3, ge=0.0)
    preamble: list[NoteRow] = Field(default_factory=list)
    footer: list[NoteRow] = Field(default_factory=list)

    def feature_names(self) -> set[str]:
        """Return the genes, which effects can target.

        Returns:
            The gene identifiers.
        """
        return set(self.feature_list())

    def mode_error(self, mode: str) -> str | None:
        """Require a sample count in the resample mode.

        Args:
            mode: ``resample`` or ``model``.

        Returns:
            The error, or None.
        """
        if mode == "resample" and self.samples is None:
            return "samples: the resample mode needs a sample count"
        return None


class ClusterSpec(StrictModel):
    """Cluster structure of an AnnData file.

    Attributes:
        column: Name of the categorical obs column that holds the clusters.
        marker_genes: Genes that each cluster raises.
        marker_log2fc: log2 fold change of a marker gene in its cluster.
    """

    column: str
    marker_genes: int = Field(default=20, ge=0)
    marker_log2fc: float = 2.0


class AnnDataDataset(NegativeBinomialFields, DatasetBase):
    """A single-cell AnnData file.

    Attributes:
        format: ``h5ad``.
        cells: Number of cells.
        cell_pattern: Shape of a cell name, such as a barcode.
        obs: Cell columns, with the tabular column kinds.
        var: Gene columns, with the tabular column kinds.
        layers: Layer names. Each layer holds the raw counts.
        obsm: Each embedding name and its dimensions, such as ``X_umap: 2``.
        uns: Names of the unstructured entries. Each is an empty mapping.
        x_kind: ``counts`` writes raw counts to X; ``log1p`` writes
            log1p(counts per 10,000).
        clusters: Cluster structure, or None.
    """

    format: Literal["h5ad"]
    cells: int = Field(ge=1)
    cell_pattern: str = "AAAAAAAAAAAAAAAA-9"
    obs: list[tabular.Column] = Field(default_factory=list)
    var: list[tabular.Column] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)
    obsm: dict[str, int] = Field(default_factory=dict)
    uns: list[str] = Field(default_factory=list)
    x_kind: Literal["counts", "log1p"] = "counts"
    clusters: ClusterSpec | None = None

    @model_validator(mode="after")
    def check_columns(self) -> AnnDataDataset:
        """Check that obs and var hold no design column and that the clusters exist.

        Returns:
            The unchanged dataset.

        Raises:
            ValueError: If a design column is present or the cluster column is
                not a categorical obs column.
        """
        if any(column.kind == "design" for column in [*self.obs, *self.var]):
            raise ValueError("obs: an AnnData file is resample only and has no design column")
        if self.clusters:
            kinds = {column.name: column.kind for column in self.obs}
            if kinds.get(self.clusters.column) != "categorical":
                raise ValueError(f"clusters: {self.clusters.column!r} is not a categorical obs column")
        return self

    def mode_error(self, mode: str) -> str | None:
        """Accept both modes: an AnnData file draws on its own in each.

        Args:
            mode: ``resample`` or ``model``.

        Returns:
            None.
        """
        return None


DATASET_MODELS = (CountMatrixDataset, AnnDataDataset)


def gene_means(log_mean_quantiles: list[float], count: int, rng: np.random.Generator) -> np.ndarray:
    """Draw one mean count per gene from the quantiles of log2(mean + 1).

    Args:
        log_mean_quantiles: Quantiles of log2(mean + 1) across genes.
        count: Number of genes.
        rng: Random stream.

    Returns:
        The gene means.
    """
    return np.maximum(2.0 ** quantile_draws(log_mean_quantiles, rng.random(count)) - 1.0, 0.0)


def gene_dispersions(means: np.ndarray, dataset: NegativeBinomialFields) -> np.ndarray:
    """Return the dispersion of each gene from the trend.

    Args:
        means: Gene means.
        dataset: The dataset with the trend.

    Returns:
        The dispersions.
    """
    trend = dataset.dispersion + dataset.dispersion_extra / np.maximum(means, 1e-3)
    return np.clip(trend, 1e-8, MAX_DISPERSION)


def size_factors(count: int, cv: float, rng: np.random.Generator) -> np.ndarray:
    """Draw library size factors with a geometric mean of 1.

    Args:
        count: Number of libraries.
        cv: Coefficient of variation.
        rng: Random stream.

    Returns:
        The size factors.
    """
    sigma = math.sqrt(math.log(1.0 + cv ** 2))
    logged = sigma * rng.standard_normal(count)
    return np.exp(logged - logged.mean())


def negative_binomial(expected: np.ndarray, dispersions: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Draw counts with given means and per-row dispersions.

    Args:
        expected: Expected counts, rows by columns.
        dispersions: Dispersion of each row.
        rng: Random stream.

    Returns:
        The counts.
    """
    size = 1.0 / dispersions[:, None]
    probability = size / (size + np.maximum(expected, 1e-12))
    return rng.negative_binomial(np.broadcast_to(size, expected.shape), probability)


def matrix_delimiter(dataset: CountMatrixDataset) -> str:
    """Return the delimiter of a count matrix file.

    Args:
        dataset: The dataset.

    Returns:
        The configured delimiter, else a tab for ``.tsv`` and ``.txt``, else a comma.
    """
    if dataset.delimiter:
        return dataset.delimiter
    return "\t" if privacy.file_suffix(Path(dataset.file)) in {".tsv", ".txt"} else ","


def count_expected(dataset: CountMatrixDataset, context: Context) -> tuple[np.ndarray, list[str]]:
    """Return the expected counts of a count matrix, genes by samples.

    Args:
        dataset: The dataset.
        context: The generation context, which collects the truth rows.

    Returns:
        The expected counts and the sample names.
    """
    config = context.config
    genes = dataset.feature_list()
    means = gene_means(dataset.log_mean_quantiles, len(genes), stream(config.seed, dataset.name, "means"))
    if config.mode == "model":
        features = [Feature(gene, lognormal_quantiles(mean, dataset.biological_cv))
                    for gene, mean in zip(genes, means, strict=True)]
        simulation = simulate(config, dataset.name, features, vectorized=True)
        context.add_truth("effects", simulation.effects)
        samples = sample_names([(row.sample.sample_id, row.replicate) for row in simulation.rows],
                               replicate_count(config, dataset.name))
        expected = simulation.values.T
    else:
        rng = stream(config.seed, dataset.name, "biological")
        sigma = math.sqrt(math.log(1.0 + dataset.biological_cv ** 2))
        noise = np.exp(sigma * rng.standard_normal((len(genes), dataset.samples)) - sigma ** 2 / 2.0)
        expected = means[:, None] * noise
        samples = invented_identifiers(dataset.sample_pattern, dataset.samples,
                                       stream(config.seed, dataset.name, "samples"))
    factors = size_factors(len(samples), dataset.library_size_cv, stream(config.seed, dataset.name, "libraries"))
    return expected * factors[None, :], samples


def generate_counts(dataset: CountMatrixDataset, context: Context) -> list[Path]:
    """Write a count matrix.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The file written.
    """
    expected, samples = count_expected(dataset, context)
    means = expected.mean(axis=1)
    counts = negative_binomial(expected, gene_dispersions(means, dataset),
                               stream(context.config.seed, dataset.name, "counts"))
    path = context.out_dir / dataset.file
    genes = dataset.feature_list()
    annotations = annotation_cells(dataset.annotation_columns, genes,
                                   stream(context.config.seed, dataset.name, "annotations"))
    write_matrix(path, dataset.feature_column, genes, annotation_names(dataset.annotation_columns), annotations,
                 samples, counts, matrix_delimiter(dataset), 0, (dataset.preamble, dataset.footer))
    return [path]


def obs_frame(columns: list[Any], count: int, index: list[str], seed: int, key: str) -> pd.DataFrame:
    """Build an obs or var frame from tabular column configs.

    Args:
        columns: The column configs.
        count: Number of rows.
        index: Row names.
        seed: The config seed.
        key: Stream key of the frame.

    Returns:
        The frame. A categorical column becomes a pandas categorical.
    """
    data = {}
    for column, name in zip(columns, tabular.column_keys(columns), strict=True):
        cells = tabular.column_cells(column, count, stream(seed, key, name))
        if column.kind == "categorical":
            data[column.name] = pd.Categorical(cells, categories=list(column.levels))
        elif column.kind == "numeric":
            data[column.name] = pd.to_numeric(pd.Series(cells, dtype=object), errors="coerce").to_numpy()
        else:
            data[column.name] = pd.Series(cells, dtype=object).astype(str).where(pd.notna(cells), None).to_numpy()
    return pd.DataFrame(data, index=pd.Index(index))


def cluster_multipliers(dataset: AnnDataDataset, obs: pd.DataFrame, gene_count: int, seed: int) -> np.ndarray:
    """Return the mean multiplier of each gene in each cell from the clusters.

    Args:
        dataset: The dataset.
        obs: The obs frame, with the cluster column.
        gene_count: Number of genes.
        seed: The config seed.

    Returns:
        Multipliers, genes by cells. All ones without clusters.
    """
    multipliers = np.ones((gene_count, len(obs)))
    if not dataset.clusters:
        return multipliers
    labels = obs[dataset.clusters.column].astype(str).to_numpy()
    for level in dict.fromkeys(labels):
        rng = stream(seed, dataset.name, "markers", level)
        markers = rng.choice(gene_count, size=min(dataset.clusters.marker_genes, gene_count), replace=False)
        cells = labels == level
        multipliers[np.ix_(markers, cells)] *= 2.0 ** dataset.clusters.marker_log2fc
    return multipliers


def embedding(cells: int, dims: int, obs: pd.DataFrame, dataset: AnnDataDataset, rng: np.random.Generator) -> np.ndarray:
    """Draw an embedding such as ``X_umap``, with one offset per cluster.

    Args:
        cells: Number of cells.
        dims: Dimensions.
        obs: The obs frame.
        dataset: The dataset.
        rng: Random stream.

    Returns:
        The embedding, cells by dimensions.
    """
    values = rng.standard_normal((cells, dims)).astype(np.float32)
    if dataset.clusters and dims:
        labels = obs[dataset.clusters.column].astype(str).to_numpy()
        for level in dict.fromkeys(labels):
            values[labels == level] += (rng.standard_normal(dims) * 4.0).astype(np.float32)
    return values


def generate_h5ad(dataset: AnnDataDataset, context: Context) -> list[Path]:
    """Write an AnnData file block by block.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The file written.
    """
    import anndata

    seed = context.config.seed
    genes = dataset.feature_list()
    names = invented_identifiers(dataset.cell_pattern, dataset.cells, stream(seed, dataset.name, "cells"))
    obs = obs_frame(dataset.obs, dataset.cells, names, seed, f"{dataset.name}/obs")
    var = obs_frame(dataset.var, len(genes), genes, seed, f"{dataset.name}/var")
    means = gene_means(dataset.log_mean_quantiles, len(genes), stream(seed, dataset.name, "means"))
    dispersions = gene_dispersions(means, dataset)
    factors = size_factors(dataset.cells, dataset.library_size_cv, stream(seed, dataset.name, "libraries"))
    multipliers = cluster_multipliers(dataset, obs, len(genes), seed)
    rng = stream(seed, dataset.name, "counts")
    blocks = []
    for start in range(0, dataset.cells, CELL_BLOCK):
        stop = min(start + CELL_BLOCK, dataset.cells)
        expected = means[:, None] * factors[None, start:stop] * multipliers[:, start:stop]
        blocks.append(sparse.csr_matrix(negative_binomial(expected, dispersions, rng).T.astype(np.float32)))
    counts = sparse.vstack(blocks).tocsr()
    matrix = counts
    if dataset.x_kind == "log1p":
        totals = np.asarray(counts.sum(axis=1)).ravel()
        matrix = sparse.diags(1e4 / np.maximum(totals, 1.0)) @ counts
        matrix.data = np.log1p(matrix.data)
        matrix = matrix.astype(np.float32).tocsr()
    adata = anndata.AnnData(X=matrix, obs=obs, var=var)
    for layer in dataset.layers:
        adata.layers[layer] = counts.copy()
    embed_rng = stream(seed, dataset.name, "obsm")
    for key, dims in dataset.obsm.items():
        adata.obsm[key] = embedding(dataset.cells, dims, obs, dataset, embed_rng)
    for key in dataset.uns:
        adata.uns[key] = {}
    adata.uns["sdg"] = {"synthetic": "true", "version": SDG_VERSION, "seed": str(seed)}
    path = context.out_dir / dataset.file
    path.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(path)
    return [path]


def generate(dataset: CountMatrixDataset | AnnDataDataset, context: Context) -> list[Path]:
    """Write one count matrix or AnnData dataset.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The file written.
    """
    if dataset.format == "h5ad":
        return generate_h5ad(dataset, context)
    return generate_counts(dataset, context)


def size_factor_estimates(counts: np.ndarray) -> np.ndarray:
    """Estimate library size factors with the median of ratios of DESeq2.

    Args:
        counts: Counts, genes by libraries.

    Returns:
        One size factor per library. Without a gene that is expressed in every
        library, the totals stand in.
    """
    positive = np.all(counts > 0, axis=1)
    if positive.sum() >= 10:
        logged = np.log(counts[positive])
        factors = np.exp(np.median(logged - logged.mean(axis=1, keepdims=True), axis=0))
    else:
        totals = counts.sum(axis=0)
        factors = totals / max(np.exp(np.mean(np.log(np.maximum(totals, 1.0)))), 1.0)
    return np.where(factors > 0, factors, 1.0)


def fit_counts(counts: np.ndarray) -> dict[str, Any]:
    """Fit the count model to real counts, as rounded summaries only.

    Args:
        counts: Counts, genes by libraries.

    Returns:
        The quantiles of log2(mean + 1), the dispersion trend and the library
        size CV, rounded to the report precision.
    """
    factors = size_factor_estimates(counts)
    normalized = counts / factors[None, :]
    means = normalized.mean(axis=1)
    variances = normalized.var(axis=1, ddof=1) if counts.shape[1] > 1 else np.zeros_like(means)
    with np.errstate(divide="ignore", invalid="ignore"):
        moments = (variances - means) / means ** 2
    high = (means > 100) & np.isfinite(moments)
    asymptotic = float(np.clip(np.median(moments[high]), 0.01, 1.0)) if high.sum() >= 10 else DEFAULT_DISPERSION
    low = (means > 1) & (means <= 100) & np.isfinite(moments)
    extra = (float(np.clip(np.median((moments[low] - asymptotic) * means[low]), 0.0, 10.0))
             if low.sum() >= 10 else DEFAULT_DISPERSION_EXTRA)
    sigma = float(np.std(np.log(factors)))
    return {"log_mean_quantiles": tabular.rounded_quantiles(list(np.log2(means + 1.0))),
            "dispersion": privacy.round_sig(asymptotic), "dispersion_extra": privacy.round_sig(extra),
            "library_size_cv": privacy.round_sig(math.sqrt(math.exp(sigma ** 2) - 1.0))}


def profile_matrix(path: Path, label: str, options: privacy.Options) -> tuple[dict[str, Any], list[str]]:
    """Profile a count matrix file.

    Args:
        path: The file.
        label: Generic file label.
        options: The profile options.

    Returns:
        The dataset config and its default fields.
    """
    matrix = read_matrix(path)
    counts = np.nan_to_num(matrix.values)
    dataset: dict[str, Any] = {"name": label.split(".", 1)[0], "format": "counts", "file": label}
    default_delimiter = "\t" if privacy.file_suffix(path) in {".tsv", ".txt"} else ","
    if matrix.delimiter != default_delimiter:
        dataset["delimiter"] = matrix.delimiter
    dataset["feature_column"] = "gene_id" if options.generic_labels else matrix.feature_column
    annotations, defaults = annotation_fields(matrix, options)
    dataset.update(annotations)
    dataset.update(feature_fields(matrix.features, options))
    dataset["samples"] = len(matrix.samples)
    dataset["sample_pattern"] = most_common_shape(matrix.samples)
    dataset.update(notes_fields(matrix))
    if len(matrix.samples) >= privacy.MIN_VALUES:
        dataset.update(fit_counts(counts))
    else:
        dataset["log_mean_quantiles"] = list(DEFAULT_LOG_MEANS)
        defaults.append("log_mean_quantiles")
    return dataset, defaults


def adata_counts(adata: Any) -> tuple[np.ndarray, str]:
    """Read the counts of the first cells of an AnnData file, genes by cells.

    Args:
        adata: The AnnData object, read in backed mode.

    Returns:
        The counts and the kind of X: ``counts`` or ``log1p``.
    """
    cells = min(adata.n_obs, PROFILE_CELLS)
    block = adata.layers["counts"][:cells] if "counts" in adata.layers else adata.X[:cells]
    dense = block.toarray() if sparse.issparse(block) else np.asarray(block)
    x_block = adata.X[:cells]
    x_values = x_block.data if sparse.issparse(x_block) else np.asarray(x_block).ravel()
    kind = "counts" if np.allclose(x_values, np.round(x_values)) else "log1p"
    if not np.allclose(dense, np.round(dense)):
        dense = np.round(np.expm1(dense))
    return dense.T, kind


def layer_names(adata: Any) -> list[str]:
    """Return the layer names of an AnnData object.

    Recent anndata versions list X as the layer ``None``, which is not a layer
    of the file. The names are sorted: an HDF5 group lists its members in name
    order unless the writer tracked the creation order, so the order is not
    part of the structure.

    Args:
        adata: The AnnData object.

    Returns:
        The names of the real layers, sorted.
    """
    return sorted(str(key) for key in adata.layers.keys() if key is not None)


def frame_stats(frame: pd.DataFrame, name: Any, options: privacy.Options) -> ColumnStats:
    """Analyze one obs or var column with the tabular rules.

    A pandas categorical column stays categorical, even when its labels look
    like numbers, as cluster labels ``0``, ``1`` and ``2`` do. An identifier
    name still wins (privacy rule 1).

    Args:
        frame: The frame.
        name: Column name.
        options: The glimpse or profile options.

    Returns:
        The column statistics.
    """
    series = frame[name]
    cells = [None if pd.isna(value) else value for value in series.astype(object).tolist()]
    cells = [value if value is None or isinstance(value, (bool, int, float)) else str(value) for value in cells]
    stats = analyze_column(str(name), cells, options.max_categories)
    if (isinstance(series.dtype, pd.CategoricalDtype) and stats.kind != "identifier"
            and 0 < stats.unique <= options.max_categories):
        stats.kind = "categorical"
        stats.numbers, stats.tokens = [], Counter()
    return stats


def frame_columns(frame: pd.DataFrame, private: bool, options: privacy.Options, file: str,
                  table: str) -> tuple[list[dict], list[str]]:
    """Profile the columns of an obs or var frame with the tabular rules.

    Args:
        frame: The frame.
        private: The frame records counts only.
        options: The profile options.
        file: Generic label of the file, for the label key.
        table: ``obs`` or ``var``, for the label key.

    Returns:
        The column configs and the paths of their default fields.
    """
    columns, defaults = [], []
    stats_list = [frame_stats(frame, name, options) for name in frame.columns]
    names = generic_columns(stats_list, options.generic_labels)
    note_columns(options, file, table, stats_list, names, private or options.generic_labels)
    for index, stats in enumerate(stats_list):
        column, defaulted = tabular.column_fields(stats, names.get(stats.name, stats.name), private,
                                                  options.generic_labels, False)
        columns.append(column)
        defaults += [f"[{index}].{item}" for item in defaulted]
    return columns, defaults


def profile_h5ad(path: Path, label: str, options: privacy.Options) -> tuple[dict[str, Any], list[str]]:
    """Profile an AnnData file.

    Args:
        path: The file.
        label: Generic file label.
        options: The profile options.

    Returns:
        The dataset config and its default fields.
    """
    import anndata

    adata = anndata.read_h5ad(path, backed="r")
    try:
        counts, kind = adata_counts(adata)
        private = bool(options.private_files.search(path.name)) or any(
            privacy.is_private_name(name) for name in adata.obs.columns)
        obs, obs_defaults = frame_columns(adata.obs, private, options, label, "obs")
        var, var_defaults = frame_columns(adata.var, False, options, label, "var")
        dataset: dict[str, Any] = {"name": label.split(".", 1)[0], "format": "h5ad", "file": label,
                                   "cells": int(adata.n_obs), "cell_pattern": most_common_shape(list(adata.obs_names))}
        dataset.update(feature_fields(list(adata.var_names), options))
        defaults = [f"obs{item}" for item in obs_defaults] + [f"var{item}" for item in var_defaults]
        if counts.shape[1] >= privacy.MIN_VALUES:
            dataset.update(fit_counts(counts))
        else:
            dataset["log_mean_quantiles"] = list(DEFAULT_LOG_MEANS)
            defaults.append("log_mean_quantiles")
        dataset.update({"obs": obs, "var": var, "layers": layer_names(adata),
                        "obsm": {key: int(value.shape[1]) for key, value in adata.obsm.items()},
                        "uns": [key for key in adata.uns.keys() if key != "sdg"], "x_kind": kind})
        cluster = next((column["name"] for column in obs
                        if column["kind"] == "categorical" and CLUSTER_NAMES.search(column["name"])), None)
        if cluster:
            dataset["clusters"] = {"column": cluster}
    finally:
        adata.file.close()
    return dataset, defaults


def profile(paths: list[Path], labels: list[str], options: privacy.Options) -> list[tuple[dict[str, Any], list[str]]]:
    """Profile count matrices and AnnData files.

    Args:
        paths: The files.
        labels: Generic label of each file.
        options: The profile options.

    Returns:
        One dataset config per file, with the paths of its default fields.
    """
    return [profile_h5ad(path, label, options) if privacy.file_suffix(path) == ".h5ad"
            else profile_matrix(path, label, options) for path, label in zip(paths, labels, strict=True)]


def glimpse_h5ad(path: Path, label: str, options: privacy.Options) -> list[TableReport]:
    """Report an AnnData file: an obs table with the matrix facts, and a var table.

    Args:
        path: The file.
        label: Generic file label.
        options: The glimpse options.

    Returns:
        The two table reports.
    """
    import anndata

    adata = anndata.read_h5ad(path, backed="r")
    try:
        counts, kind = adata_counts(adata)
        private = bool(options.private_files.search(path.name)) or any(
            privacy.is_private_name(name) for name in adata.obs.columns)
        # Sorted like the layers: the HDF5 member order is not structure.
        obsm = ", ".join(f"{key!r} {adata.obsm[key].shape[1]}" for key in sorted(adata.obsm.keys()))
        facts = [Fact("rows", str(adata.n_obs)), Fact("features", str(adata.n_vars)), Fact("x kind", kind),
                 Fact("layers", ", ".join(repr(key) for key in layer_names(adata)) or "-"),
                 Fact("obsm", obsm or "-"),
                 Fact("uns", ", ".join(repr(key) for key in sorted(adata.uns.keys()) if key != "sdg") or "-")]
        if counts.size:
            facts.append(Fact("zero share", privacy.format_number(float((counts == 0).mean()))))
        if private:
            facts.append(Fact("private", "counts only"))
        tables = [frame_report(label, "obs", adata.obs, facts, private, options),
                  frame_report(label, "var", adata.var, [Fact("rows", str(adata.n_vars))], False, options)]
    finally:
        adata.file.close()
    return tables


def frame_report(file: str, table: str, frame: pd.DataFrame, facts: list[Fact], private: bool,
                 options: privacy.Options) -> TableReport:
    """Report the columns of an obs or var frame.

    Args:
        file: Generic label of the file.
        table: ``obs`` or ``var``.
        frame: The frame.
        facts: Table facts.
        private: The frame records counts only.
        options: The glimpse options.

    Returns:
        The table report.
    """
    report = TableReport(f"{file} | {table}", "h5ad", facts)
    stats_list = [frame_stats(frame, name, options) for name in frame.columns]
    names = generic_columns(stats_list, options.generic_labels)
    note_columns(options, file, table, stats_list, names, options.generic_labels and not private)
    for stats in stats_list:
        levels = level_names(stats) if options.generic_labels and stats.kind == "categorical" else None
        report.columns.append(column_report(stats, private, options.max_categories, names.get(stats.name), levels))
    return report


def glimpse(paths: list[Path], labels: list[str], options: privacy.Options) -> list[TableReport]:
    """Report count matrices and AnnData files.

    Args:
        paths: The files.
        labels: Generic label of each file.
        options: The glimpse options.

    Returns:
        The table reports.
    """
    reports = []
    for path, label in zip(paths, labels, strict=True):
        if privacy.file_suffix(path) == ".h5ad":
            reports += glimpse_h5ad(path, label, options)
            continue
        matrix = read_matrix(path)
        counts = np.nan_to_num(matrix.values)
        extra = [Fact("integer", "yes" if np.allclose(counts, np.round(counts)) else "no"),
                 Fact("zero share", privacy.format_number(float((counts == 0).mean())))]
        reports.append(matrix_report(label, "counts", matrix, options, extra))
    return reports


def audit_values(paths: list[Path], options: privacy.Options) -> dict[str, str]:
    """Collect the sample and cell names, which are identifiers.

    Args:
        paths: The real files.
        options: The options that made the checked text.

    Returns:
        Each sensitive value mapped to its source kind.
    """
    import anndata

    found: dict[str, str] = {}
    for path in paths:
        if privacy.file_suffix(path) == ".h5ad":
            adata = anndata.read_h5ad(path, backed="r")
            try:
                found.update({str(name): "cell name" for name in adata.obs_names})
                for name in adata.obs.columns:
                    stats = analyze_column(str(name), adata.obs[name].astype(object).tolist())
                    if stats.kind in tabular.AUDITED_KINDS:
                        found.update({value: tabular.AUDITED_KINDS[stats.kind] for value in stats.values})
            finally:
                adata.file.close()
        else:
            matrix = read_matrix(path)
            found.update(note_values([*matrix.preamble, *matrix.footer]))
            found.update({name: "sample name" for name in matrix.samples})
            if options.generic_labels:
                found.update({name: "feature name" for name in matrix.features})
    return found


COUNTS_TEMPLATE = '''\
  # A bulk count matrix: genes in rows, samples in columns. The model mode
  # takes the samples of the design; the resample mode needs samples.
  - name: {name}
    format: counts
    file: {name}.tsv
    feature_column: gene_id
    feature_count: 2000               # or features: [ENSG00000141510, ...]
    feature_prefix: "gene_"
{samples}    log_mean_quantiles: [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.5, 9.0, 13.0]  # log2(mean + 1) across genes
    dispersion: 0.05                  # dispersion = dispersion + dispersion_extra / mean
    dispersion_extra: 1.0
    library_size_cv: 0.2
    biological_cv: 0.3                # spread of a gene mean between samples
'''

H5AD_TEMPLATE = '''\
  # A single-cell AnnData file. It draws on its own in both modes.
  - name: {name}
    format: h5ad
    file: {name}.h5ad
    cells: 1000
    feature_count: 500
    log_mean_quantiles: [0.0, 0.0, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0, 6.0]
    dispersion: 0.1
    dispersion_extra: 1.0
    library_size_cv: 0.5
    obs:                              # cell columns, with the tabular column kinds
      - name: sample
        kind: categorical
        levels: {{sample_1: 0.5, sample_2: 0.5}}
      - name: leiden
        kind: categorical
        levels: {{"0": 0.4, "1": 0.35, "2": 0.25}}
    layers: [counts]                  # each layer holds the raw counts
    obsm: {{X_pca: 50, X_umap: 2}}
    x_kind: log1p                     # counts, or log1p(counts per 10,000)
    clusters: {{column: leiden, marker_genes: 20, marker_log2fc: 2.0}}
'''


def template(fmt: str, mode: str, name: str) -> tuple[str, str | None]:
    """Return the commented template of a count matrix or an AnnData dataset.

    Args:
        fmt: ``counts`` or ``h5ad``.
        mode: ``resample`` or ``model``.
        name: Dataset name.

    Returns:
        The YAML list item, and the feature that a template effect can target.
    """
    if fmt == "h5ad":
        return H5AD_TEMPLATE.format(name=name), None
    samples = "" if mode == "model" else "    samples: 12\n    sample_pattern: SMPL-99  # A is a letter, 9 a digit; other characters stay\n"
    return COUNTS_TEMPLATE.format(name=name, samples=samples), "gene_1" if mode == "model" else None


def check_output(dataset: CountMatrixDataset | AnnDataDataset, config: Any, out_dir: Path) -> list[str]:
    """Compare the output file with the config: features, samples and cells.

    Args:
        dataset: The dataset config.
        config: The whole config.
        out_dir: The output folder.

    Returns:
        The problems found.
    """
    import anndata

    path = out_dir / dataset.file
    if not path.is_file():
        return [f"{dataset.file}: missing"]
    if dataset.format == "h5ad":
        adata = anndata.read_h5ad(path, backed="r")
        try:
            issues = []
            if adata.shape != (dataset.cells, len(dataset.feature_list())):
                issues.append(f"{dataset.file}: shape {adata.shape}, the config gives "
                              f"{(dataset.cells, len(dataset.feature_list()))}")
            if list(adata.obs.columns) != [column.name for column in dataset.obs]:
                issues.append(f"{dataset.file}: obs columns {list(adata.obs.columns)} differ from the config")
            if layer_names(adata) != sorted(dataset.layers):
                issues.append(f"{dataset.file}: layers {layer_names(adata)} differ from the config")
        finally:
            adata.file.close()
        return issues
    matrix = read_matrix(path)
    issues = []
    if matrix.features != dataset.feature_list():
        issues.append(f"{dataset.file}: the features differ from the config")
    if len(matrix.samples) != sample_count(dataset, config):
        issues.append(f"{dataset.file}: {len(matrix.samples)} samples, the config gives {sample_count(dataset, config)}")
    return issues + notes_issues(dataset.file, matrix, dataset)


def observed(dataset: CountMatrixDataset | AnnDataDataset, out_dir: Path) -> dict[tuple[str, str], list[float]] | None:
    """Read the normalized counts back, by sample and gene.

    Args:
        dataset: The dataset config.
        out_dir: The output folder.

    Returns:
        Counts divided by the size factor of their sample, or None for an
        AnnData file, which has no design samples.
    """
    if dataset.format == "h5ad":
        return None
    matrix = read_matrix(out_dir / dataset.file)
    counts = np.nan_to_num(matrix.values)
    normalized = counts / size_factor_estimates(counts)[None, :]
    found: dict[tuple[str, str], list[float]] = {}
    for column, sample in enumerate(matrix.samples):
        for row, feature in enumerate(matrix.features):
            found.setdefault((strip_replicate(sample), feature), []).append(float(normalized[row, column]))
    return found
