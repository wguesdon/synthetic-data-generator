"""Processed microarray matrices: log2 intensities, probes in rows, samples in columns.

Version 1 reads the processed matrix only; raw CEL files need Bioconductor.
The config holds the quantiles of the probe means and of the
probe standard deviations across probes, and the spread of an array shift,
never the value of one probe. Probe identifiers and the platform are public, so
they stay unless ``--generic-labels`` is set.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import Field, field_validator, model_validator

from sdg import privacy
from sdg.config_base import DatasetBase, NoteRow
from sdg.formats import tabular
from sdg.matrix import (Annotation, annotation_cells, annotation_fields, annotation_names, feature_fields,
                        generated_features, matrix_report, most_common_shape, notes_fields, notes_issues, read_matrix,
                        sample_names, strip_replicate, write_matrix)
from sdg.tables import note_values
from sdg.modes.model import Feature, normal_quantiles, replicate_count, sample_count, simulate
from sdg.modes.resample import invented_identifiers, quantile_draws
from sdg.report import Fact, TableReport
from sdg.seeding import stream

if TYPE_CHECKING:
    from sdg.generate import Context

FORMATS = ("microarray",)

#: A processed matrix has the suffix of a plain table, so only ``--format
#: microarray`` selects this module.
SUFFIXES: tuple[str, ...] = ()

#: The largest value of a matrix on the log2 scale.
LOG2_MAX = 30.0

#: Defaults where the privacy rules forbid a measurement.
DEFAULT_MEANS = [3.0, 4.0, 4.6, 5.2, 5.8, 6.5, 7.2, 8.0, 9.0, 10.5, 14.0]
DEFAULT_SDS = [0.05, 0.1, 0.12, 0.15, 0.18, 0.21, 0.25, 0.3, 0.38, 0.5, 1.5]


class MicroarrayDataset(DatasetBase):
    """A processed microarray matrix.

    Attributes:
        format: ``microarray``.
        platform: Platform accession, such as ``GPL570``, or None.
        delimiter: Delimiter. ``None`` uses a tab for ``.tsv`` and ``.txt``, else a comma.
        feature_column: Header of the probe identifier column.
        annotation_columns: Columns after the identifier: the header of a text
            column, such as a gene symbol, which repeats the probe identifier,
            or a numeric column with its quantiles.
        features: Probe identifiers. Empty generates ``feature_count`` names.
        feature_count: Number of probes when ``features`` is empty.
        feature_prefix: Prefix of a generated probe name.
        samples: Sample count in the resample mode.
        sample_pattern: Shape of a sample column header, such as ``GSM9999999``.
        mean_quantiles: Quantiles of the probe means across probes, log2.
        sd_quantiles: Quantiles of the probe standard deviations across probes.
        array_shift_sd: Standard deviation of the shift of a whole array.
        decimals: Decimal places of a written value.
        preamble: Rows above the header, such as the metadata lines of a GEO
            series matrix.
        footer: Comment rows below the data, such as the end line of a GEO
            series matrix.
    """

    format: Literal["microarray"]
    platform: str | None = None
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)
    feature_column: str = "ID_REF"
    annotation_columns: list[Annotation] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    feature_count: int | None = Field(default=None, ge=1)
    feature_prefix: str = "probe_"
    samples: int | None = Field(default=None, ge=1)
    sample_pattern: str = "GSM9999999"
    mean_quantiles: list[float] = Field(min_length=2)
    sd_quantiles: list[float] = Field(min_length=2)
    array_shift_sd: float = Field(default=0.1, ge=0.0)
    decimals: int | None = Field(default=3, ge=0, le=12)
    preamble: list[NoteRow] = Field(default_factory=list)
    footer: list[NoteRow] = Field(default_factory=list)

    check_means = field_validator("mean_quantiles", "sd_quantiles")(tabular.check_non_decreasing)

    @model_validator(mode="after")
    def check_feature_source(self) -> MicroarrayDataset:
        """Check that the probes come from a list or a count.

        Returns:
            The unchanged dataset.

        Raises:
            ValueError: If both or neither are set.
        """
        if bool(self.features) == (self.feature_count is not None):
            raise ValueError("features: give a feature list or a feature_count, not both")
        return self

    def feature_list(self) -> list[str]:
        """Return the probe names in order.

        Returns:
            The listed probes, or the generated ones.
        """
        return generated_features(self.features, self.feature_count, self.feature_prefix)

    def feature_names(self) -> set[str]:
        """Return the probes, which effects can target.

        Returns:
            The probe identifiers.
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


DATASET_MODELS = (MicroarrayDataset,)


def probe_parameters(dataset: MicroarrayDataset, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Draw the mean and the standard deviation of each probe.

    Args:
        dataset: The dataset.
        seed: The config seed.

    Returns:
        The probe means and standard deviations.
    """
    rng = stream(seed, dataset.name, "probes")
    count = len(dataset.feature_list())
    return (quantile_draws(dataset.mean_quantiles, rng.random(count)),
            quantile_draws(dataset.sd_quantiles, rng.random(count)))


def generate(dataset: MicroarrayDataset, context: Context) -> list[Path]:
    """Write a microarray matrix.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The file written.
    """
    config = context.config
    probes = dataset.feature_list()
    means, sds = probe_parameters(dataset, config.seed)
    if config.mode == "model":
        features = [Feature(probe, normal_quantiles(mean, sd), "log2")
                    for probe, mean, sd in zip(probes, means, sds, strict=True)]
        simulation = simulate(config, dataset.name, features, vectorized=True)
        context.add_truth("effects", simulation.effects)
        values = simulation.values.T
        samples = sample_names([(row.sample.sample_id, row.replicate) for row in simulation.rows],
                               replicate_count(config, dataset.name))
    else:
        rng = stream(config.seed, dataset.name, "values")
        values = means[:, None] + sds[:, None] * rng.standard_normal((len(probes), dataset.samples))
        samples = invented_identifiers(dataset.sample_pattern, dataset.samples,
                                       stream(config.seed, dataset.name, "samples"))
    shifts = dataset.array_shift_sd * stream(config.seed, dataset.name, "arrays").standard_normal(len(samples))
    delimiter = dataset.delimiter or ("\t" if privacy.file_suffix(Path(dataset.file)) in {".tsv", ".txt"} else ",")
    path = context.out_dir / dataset.file
    annotations = annotation_cells(dataset.annotation_columns, probes, stream(config.seed, dataset.name, "annotations"))
    write_matrix(path, dataset.feature_column, probes, annotation_names(dataset.annotation_columns), annotations,
                 samples, values + shifts[None, :], delimiter, dataset.decimals, (dataset.preamble, dataset.footer))
    return [path]


def fit_matrix(values: np.ndarray) -> dict[str, Any]:
    """Fit the probe model to a real matrix, as rounded summaries only.

    Args:
        values: Values, probes by samples.

    Returns:
        The quantiles of the probe means and standard deviations, and the
        spread of the array shifts.
    """
    medians = np.nanmedian(values, axis=0)
    shifted = values - (medians - np.nanmedian(medians))[None, :]
    return {"mean_quantiles": tabular.rounded_quantiles(list(np.nanmean(shifted, axis=1))),
            "sd_quantiles": tabular.rounded_quantiles(list(np.nanstd(shifted, axis=1, ddof=1))),
            "array_shift_sd": privacy.round_sig(float(np.std(medians, ddof=1)))}


def profile(paths: list[Path], labels: list[str], options: privacy.Options) -> list[tuple[dict[str, Any], list[str]]]:
    """Profile processed microarray matrices.

    Args:
        paths: The files.
        labels: Generic label of each file.
        options: The profile options. ``--set platform=GPL570`` records the platform.

    Returns:
        One dataset config per file, with the paths of its default fields.
    """
    datasets = []
    for path, label in zip(paths, labels, strict=True):
        matrix = read_matrix(path)
        dataset: dict[str, Any] = {"name": label.split(".", 1)[0], "format": "microarray", "file": label,
                                   "platform": options.extra.get("platform")}
        default_delimiter = "\t" if privacy.file_suffix(path) in {".tsv", ".txt"} else ","
        if matrix.delimiter != default_delimiter:
            dataset["delimiter"] = matrix.delimiter
        dataset["feature_column"] = "ID_REF" if options.generic_labels else matrix.feature_column
        annotations, defaults = annotation_fields(matrix, options)
        dataset.update(annotations)
        dataset.update(feature_fields(matrix.features, options))
        dataset.update({"samples": len(matrix.samples), "sample_pattern": most_common_shape(matrix.samples)})
        dataset.update(notes_fields(matrix))
        if len(matrix.samples) >= privacy.MIN_VALUES:
            dataset.update(fit_matrix(matrix.values))
        else:
            dataset.update({"mean_quantiles": list(DEFAULT_MEANS), "sd_quantiles": list(DEFAULT_SDS)})
            defaults += ["mean_quantiles", "sd_quantiles"]
        places = [decimal_places(value) for value in matrix.values[:200].ravel() if np.isfinite(value)]
        dataset["decimals"] = max(places) if places else None
        datasets.append((dataset, defaults))
    return datasets


def decimal_places(value: float) -> int:
    """Count the decimal places of a value, up to six.

    Args:
        value: A number.

    Returns:
        The decimal places.
    """
    text = f"{value:.6f}".rstrip("0")
    return len(text.split(".", 1)[1]) if "." in text else 0


def glimpse(paths: list[Path], labels: list[str], options: privacy.Options) -> list[TableReport]:
    """Report processed microarray matrices.

    Args:
        paths: The files.
        labels: Generic label of each file.
        options: The glimpse options.

    Returns:
        One table report per file.
    """
    reports = []
    for path, label in zip(paths, labels, strict=True):
        matrix = read_matrix(path)
        finite = matrix.values[np.isfinite(matrix.values)]
        extra = [Fact("log2", "yes" if finite.size and finite.max() <= LOG2_MAX else "no"),
                 Fact("missing values", str(int((~np.isfinite(matrix.values)).sum())))]
        if len(matrix.samples) >= privacy.MIN_VALUES and finite.size:
            extra.append(Fact("range", f"{privacy.format_number(float(finite.min()))} to "
                                       f"{privacy.format_number(float(finite.max()))}"))
        reports.append(matrix_report(label, "microarray", matrix, options, extra))
    return reports


def audit_values(paths: list[Path], options: privacy.Options) -> dict[str, str]:
    """Collect the sample names, which are identifiers.

    Args:
        paths: The real files.
        options: The options that made the checked text.

    Returns:
        Each sensitive value mapped to its source kind.
    """
    found: dict[str, str] = {}
    for path in paths:
        matrix = read_matrix(path)
        found.update(note_values([*matrix.preamble, *matrix.footer]))
        found.update({name: "sample name" for name in matrix.samples})
        if options.generic_labels:
            found.update({name: "feature name" for name in matrix.features})
    return found


TEMPLATE = '''\
  # A processed microarray matrix: log2 intensities, probes in rows, samples in
  # columns. The model mode takes the samples of the design.
  - name: {name}
    format: microarray
    file: {name}.tsv
    platform: GPL570                  # platform accession, or null
    feature_column: ID_REF
    feature_count: 1000               # or features: ["1007_s_at", ...]
    feature_prefix: "probe_"
{samples}    mean_quantiles: [3.0, 4.0, 4.6, 5.2, 5.8, 6.5, 7.2, 8.0, 9.0, 10.5, 14.0]  # probe means, log2
    sd_quantiles: [0.05, 0.1, 0.12, 0.15, 0.18, 0.21, 0.25, 0.3, 0.38, 0.5, 1.5]
    array_shift_sd: 0.1               # shift of a whole array
    decimals: 3
'''


def template(fmt: str, mode: str, name: str) -> tuple[str, str | None]:
    """Return the commented template of a microarray dataset.

    Args:
        fmt: ``microarray``.
        mode: ``resample`` or ``model``.
        name: Dataset name.

    Returns:
        The YAML list item, and the feature that a template effect can target.
    """
    samples = "" if mode == "model" else "    samples: 12\n    sample_pattern: GSM9999999\n"
    return TEMPLATE.format(name=name, samples=samples), "probe_1" if mode == "model" else None


def check_output(dataset: MicroarrayDataset, config: Any, out_dir: Path) -> list[str]:
    """Compare the output matrix with the config: probes and samples.

    Args:
        dataset: The dataset config.
        config: The whole config.
        out_dir: The output folder.

    Returns:
        The problems found.
    """
    path = out_dir / dataset.file
    if not path.is_file():
        return [f"{dataset.file}: missing"]
    matrix = read_matrix(path)
    expected = sample_count(dataset, config)
    issues = []
    if matrix.features != dataset.feature_list():
        issues.append(f"{dataset.file}: the probes differ from the config")
    if len(matrix.samples) != expected:
        issues.append(f"{dataset.file}: {len(matrix.samples)} samples, the config gives {expected}")
    return issues + notes_issues(dataset.file, matrix, dataset)


def observed(dataset: MicroarrayDataset, out_dir: Path) -> dict[tuple[str, str], list[float]]:
    """Read the log2 intensities back, by sample and probe.

    Args:
        dataset: The dataset config.
        out_dir: The output folder.

    Returns:
        The values of each sample and probe.
    """
    matrix = read_matrix(out_dir / dataset.file)
    found: dict[tuple[str, str], list[float]] = {}
    for column, sample in enumerate(matrix.samples):
        for row, feature in enumerate(matrix.features):
            if np.isfinite(matrix.values[row, column]):
                found.setdefault((strip_replicate(sample), feature), []).append(float(matrix.values[row, column]))
    return found
