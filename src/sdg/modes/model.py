"""The model mode: feature values from the study design, with effects and artefacts.

The model is a mixed model. Each subject
draws one random intercept per feature and carries it to all of its samples;
each sample adds its own residual. The latent value

    z = sqrt(icc) * subject + sqrt(1 - icc) * residual

is standard normal, so mapping it through the normal CDF and then through the
quantiles of the feature gives the marginal distribution that the config
records. Effects, replicates and censoring apply after that mapping, and every
one of them goes to a truth table, so a pipeline run can be checked against a
known answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy.special import ndtr, ndtri

from sdg.design import DesignRow, Sample, all_samples, design_rows
from sdg.modes.resample import quantile_draws
from sdg.privacy import round_sig
from sdg.seeding import stream

if TYPE_CHECKING:
    from sdg.config import Config, Effect


#: Significant digits of a value in a truth table.
TRUTH_DIGITS = 6

#: Probabilities of the quantiles that stand for a parametric distribution.
#: The ends are not 0 and 1, where a log-normal or a normal has no finite value.
PARAMETRIC_PROBABILITIES = np.concatenate([[0.001], np.linspace(0.1, 0.9, 9), [0.999]])


def lognormal_quantiles(mean: float, cv: float) -> list[float]:
    """Return the quantiles of a log-normal distribution with a mean and a CV.

    A matrix format records one mean per feature, not quantiles. This turns
    the mean into the quantiles that the model maps the latent value through.

    Args:
        mean: Mean of the distribution.
        cv: Coefficient of variation, 0 for a constant.

    Returns:
        Eleven quantiles.
    """
    sigma = math.sqrt(math.log(1.0 + cv ** 2))
    return list(mean * np.exp(sigma * ndtri(PARAMETRIC_PROBABILITIES) - sigma ** 2 / 2.0))


def normal_quantiles(mean: float, sd: float) -> list[float]:
    """Return the quantiles of a normal distribution, such as a log2 intensity.

    Args:
        mean: Mean of the distribution.
        sd: Standard deviation.

    Returns:
        Eleven quantiles.
    """
    return list(mean + sd * ndtri(PARAMETRIC_PROBABILITIES))


@dataclass(frozen=True)
class Feature:
    """One simulated feature.

    Attributes:
        name: Feature name, which effects use.
        quantiles: Values at evenly spaced probabilities, the reference
            distribution with no effect.
        scale: ``linear``: an effect multiplies the value. ``log2``: an effect
            adds the log2 fold change, as for NPX or log2 intensities.
    """

    name: str
    quantiles: list[float]
    scale: str = "linear"


@dataclass
class Simulation:
    """The values of one dataset in the model mode.

    Attributes:
        rows: The measured rows, one per present sample and replicate.
        features: The features, one per column of ``values``.
        values: True values, rows by features, before censoring.
        censored: True where a value is below the lower limit.
        limits: Lower limit of each feature, or NaN when nothing is censored.
        effects: Rows of ``truth/effects.csv``.
        censored_truth: Rows of ``truth/censored.csv``.
    """

    rows: list[DesignRow]
    features: list[Feature]
    values: np.ndarray
    censored: np.ndarray
    limits: np.ndarray
    effects: list[dict[str, Any]] = field(default_factory=list)
    censored_truth: list[dict[str, Any]] = field(default_factory=list)


def applies_to(config: Config, dataset: str) -> bool:
    """Say whether the artefacts of the config apply to a dataset.

    Args:
        config: The config.
        dataset: Dataset name.

    Returns:
        True when the config has artefacts and they name the dataset or name
        no dataset.
    """
    artefacts = config.artefacts
    return artefacts is not None and (not artefacts.datasets or dataset in artefacts.datasets)


def replicate_count(config: Config, dataset: str) -> int:
    """Return the number of replicates of each sample of a dataset.

    Args:
        config: The config.
        dataset: Dataset name.

    Returns:
        The replicate count, 1 when the dataset has no replicates.
    """
    if applies_to(config, dataset) and config.artefacts.replicates:
        return config.artefacts.replicates.n
    return 1


def latent_matrix(config: Config, key: str, feature_count: int, samples: list[Sample]) -> np.ndarray:
    """Draw the latent values of every feature from one stream per part.

    A matrix format, such as a count matrix with 60,000 genes, cannot afford
    one stream per feature. Its feature list is fixed, so one stream for the
    subject part and one for the residual part keep the data reproducible.

    Args:
        config: The config. Its design must be set.
        key: Stream key of the dataset.
        feature_count: Number of features.
        samples: Every sample of the design.

    Returns:
        One latent value per sample and feature.
    """
    icc = config.design.icc
    subject_count = max(sample.subject_index for sample in samples) + 1
    between = stream(config.seed, key, "subject").standard_normal((subject_count, feature_count))
    within = stream(config.seed, key, "residual").standard_normal((len(samples), feature_count))
    subject_of = np.array([sample.subject_index for sample in samples])
    return math.sqrt(icc) * between[subject_of] + math.sqrt(1.0 - icc) * within


def latent_values(config: Config, key: str, feature: Feature, samples: list[Sample]) -> np.ndarray:
    """Draw the standard normal latent value of each sample for one feature.

    Draws cover every sample, present or absent, so a change in the absent
    samples leaves the values of the others unchanged.

    Args:
        config: The config. Its design must be set.
        key: Stream key of the dataset.
        feature: The feature.
        samples: Every sample of the design.

    Returns:
        One latent value per sample.
    """
    icc = config.design.icc
    subject_count = max(sample.subject_index for sample in samples) + 1
    between = stream(config.seed, key, "subject", feature.name).standard_normal(subject_count)
    within = stream(config.seed, key, "residual", feature.name).standard_normal(len(samples))
    subject_of = np.array([sample.subject_index for sample in samples])
    return math.sqrt(icc) * between[subject_of] + math.sqrt(1.0 - icc) * within


def effect_mask(effect: Effect, rows: list[DesignRow]) -> np.ndarray:
    """Select the rows that an effect moves.

    Args:
        effect: The effect.
        rows: The measured rows.

    Returns:
        True for each row of the moved group at an effect timepoint.
    """
    return np.array([row.sample.group == effect.contrast[0]
                     and (not effect.timepoints or row.sample.timepoint in effect.timepoints)
                     for row in rows], dtype=bool)


def effect_truth(config: Config, effect: Effect, applied: float, scale: str) -> list[dict[str, Any]]:
    """Return the truth rows of one effect, one per timepoint where it applies.

    Args:
        config: The config. Its design must be set.
        effect: The effect.
        applied: The fold change that was applied, 1.0 in a null dataset.
        scale: Scale of the feature.

    Returns:
        The rows of ``truth/effects.csv``.
    """
    timepoints = effect.timepoints or config.design.timepoints or [""]
    return [{"dataset": effect.dataset, "feature": effect.feature, "group": effect.contrast[0],
             "reference": effect.contrast[1], "timepoint": timepoint, "fold_change": applied,
             "log2_fold_change": round(math.log2(applied), 6), "planned_fold_change": effect.fold_change,
             "scale": scale} for timepoint in timepoints]


def apply_effects(config: Config, dataset: str, simulation: Simulation) -> None:
    """Move the values of each effect on the dataset, and record the truth.

    Args:
        config: The config.
        dataset: Dataset name, which effects use.
        simulation: The simulation to change in place.
    """
    index = {feature.name: position for position, feature in enumerate(simulation.features)}
    for effect in config.effects:
        if effect.dataset != dataset or effect.feature not in index:
            continue
        column = index[effect.feature]
        applied = 1.0 if config.null else effect.fold_change
        mask = effect_mask(effect, simulation.rows)
        scale = simulation.features[column].scale
        if scale == "log2":
            simulation.values[mask, column] += math.log2(applied)
        else:
            simulation.values[mask, column] *= applied
        simulation.effects += effect_truth(config, effect, applied, scale)


def apply_replicates(config: Config, key: str, simulation: Simulation, sample_count: int,
                     vectorized: bool = False) -> None:
    """Add replicate noise with the configured coefficient of variation.

    On the linear scale the noise is a log-normal factor with mean 1; on the
    log2 scale it is the matching normal shift.

    Args:
        config: The config. Its artefacts must have replicates.
        key: Stream key of the dataset.
        simulation: The simulation to change in place.
        sample_count: Number of samples in the design, absent ones included.
        vectorized: Draw all features from one stream, for matrix formats.
    """
    replicates = config.artefacts.replicates
    sigma = math.sqrt(math.log(1.0 + (replicates.cv_pct / 100.0) ** 2))
    positions = np.array([row.position for row in simulation.rows])
    numbers = np.array([row.replicate - 1 for row in simulation.rows])
    if vectorized:
        noise = stream(config.seed, key, "replicate").standard_normal(
            (sample_count, replicates.n, len(simulation.features)))
        drawn_all = noise[positions, numbers, :]
    for column, feature in enumerate(simulation.features):
        if vectorized:
            drawn = drawn_all[:, column]
        else:
            noise = stream(config.seed, key, "replicate", feature.name).standard_normal((sample_count, replicates.n))
            drawn = noise[positions, numbers]
        if feature.scale == "log2":
            simulation.values[:, column] += sigma * drawn / math.log(2.0)
        else:
            simulation.values[:, column] *= np.exp(sigma * drawn - sigma ** 2 / 2.0)


def apply_censoring(config: Config, dataset: str, simulation: Simulation) -> None:
    """Mark the values below the lower limit of each feature.

    The limit is the configured quantile of the reference distribution, so
    the censored share is near that quantile when no effect moves the feature.

    Args:
        config: The config. Its artefacts must have a lower limit quantile.
        dataset: Dataset name.
        simulation: The simulation to change in place.
    """
    share = config.artefacts.lower_limit_quantile
    simulation.limits = np.array([quantile_draws(feature.quantiles, np.array([share]))[0]
                                  for feature in simulation.features])
    simulation.censored = simulation.values < simulation.limits
    for row_index, column in zip(*np.nonzero(simulation.censored), strict=True):
        row = simulation.rows[row_index]
        simulation.censored_truth.append({
            "dataset": dataset, "sample_id": row.sample.sample_id, "replicate": row.replicate,
            "feature": simulation.features[column].name,
            "value": round_sig(float(simulation.values[row_index, column]), TRUTH_DIGITS),
            "limit": round_sig(float(simulation.limits[column]), TRUTH_DIGITS)})


def simulate(config: Config, dataset: str, features: list[Feature], key: str | None = None,
             vectorized: bool = False) -> Simulation:
    """Simulate the values of one dataset in the model mode.

    Args:
        config: The config. Its design must be set.
        dataset: Dataset name, which effects and artefacts use.
        features: The features to simulate.
        key: Stream key, such as ``dataset/sheet`` when a dataset has several
            tables. ``None`` uses the dataset name.
        vectorized: Draw all features from one stream per part, for a matrix
            format with many features. Then a new feature changes the values
            of the others.

    Returns:
        The simulation, with its truth rows.
    """
    key = key or dataset
    samples = all_samples(config)
    rows = design_rows(config, replicate_count(config, dataset))
    positions = np.array([row.position for row in rows], dtype=int)
    values = np.empty((len(rows), len(features)))
    latent_all = ndtr(latent_matrix(config, key, len(features), samples)) if vectorized else None
    for column, feature in enumerate(features):
        latent = latent_all[:, column] if vectorized else ndtr(latent_values(config, key, feature, samples))
        values[:, column] = quantile_draws(feature.quantiles, latent)[positions]
    simulation = Simulation(rows, features, values, np.zeros(values.shape, dtype=bool),
                            np.full(len(features), np.nan))
    apply_effects(config, dataset, simulation)
    if replicate_count(config, dataset) > 1:
        apply_replicates(config, key, simulation, len(samples), vectorized)
    if applies_to(config, dataset) and config.artefacts.lower_limit_quantile > 0:
        apply_censoring(config, dataset, simulation)
    return simulation


def measured_rows(config: Config, dataset: str) -> int:
    """Return the number of measured rows of a model-mode dataset.

    Args:
        config: The config. Its design must be set.
        dataset: Dataset name.

    Returns:
        Present samples times replicates.
    """
    return len(design_rows(config, replicate_count(config, dataset)))


def sample_count(dataset: Any, config: Config) -> int:
    """Return the number of sample columns or files that a dataset must have.

    Args:
        dataset: A dataset with ``samples``.
        config: The whole config.

    Returns:
        The configured samples, or the measured rows of the model mode.
    """
    return measured_rows(config, dataset.name) if config.mode == "model" else dataset.samples
