"""Flow cytometry FCS files: a folder with one file per sample.

Channel names (``$PnN``), marker labels (``$PnS``) and ranges stay, because the
analysis code uses them. Every other keyword keeps its name only, and its value
becomes a generic token (privacy rule 8).

Events come from a mixture of populations on the arcsinh scale. A profile
finds the populations with k-means on events pooled from all files, and
records each population as its share, its centre and its spread per channel:
aggregates over many events, never one event. Gating code therefore finds
populations in the synthetic files. In the model mode, a feature is a
population, and an effect multiplies its frequency in the moved group.
``sdg validate`` reads the frequencies back by assigning each event to the
population with the highest likelihood.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import Field, field_validator, model_validator

from sdg import privacy
from sdg.config_base import DatasetBase, Share, StrictModel
from sdg.formats import tabular
from sdg.matrix import most_common_shape, sample_names, strip_replicate
from sdg.modes.model import Feature, lognormal_quantiles, replicate_count, sample_count, simulate
from sdg.modes.resample import invented_identifiers, quantile_draws
from sdg.report import ColumnReport, Fact, TableReport
from sdg.seeding import stream
from sdg.version import SDG_VERSION

if TYPE_CHECKING:
    from sdg.generate import Context

FORMATS = ("fcs",)
SUFFIXES = (".fcs",)

#: Standard keywords that describe the file layout or a channel; they are
#: written by the FCS writer, so a profile does not list them.
LAYOUT_KEYWORDS = re.compile(r"^(p\d+[a-z]+|par|tot|byteord|datatype|mode|nextdata|begin\w*|end\w*|fcsversion)$")

#: A channel with this name holds the acquisition time.
TIME_CHANNEL = re.compile(r"^time$", re.IGNORECASE)

#: Events per file that a profile pools for the clustering.
POOL_EVENTS = 2000

#: Default number of populations of a profile; ``--set populations=8`` changes it.
DEFAULT_POPULATIONS = 6

#: Iterations of k-means.
KMEANS_ROUNDS = 50

#: Defaults where the privacy rules forbid a measurement.
DEFAULT_EVENTS = [10000.0, 50000.0]

#: Smallest spread that the read-back classification uses, so a channel with
#: no spread in one population does not divide by zero.
MIN_SPREAD = 0.05


class FcsChannel(StrictModel):
    """One channel.

    Attributes:
        name: Channel name, ``$PnN``.
        label: Marker label, ``$PnS``, or None.
        range: Channel range, ``$PnR``.
    """

    name: str = Field(min_length=1)
    label: str | None = None
    range: float = Field(default=262144.0, gt=0.0)


class FcsPopulation(StrictModel):
    """One population of events.

    Attributes:
        name: Population name, which effects use.
        weight: Share of the events in the population.
        means: Centre of the population on the arcsinh scale, one per channel.
        sds: Spread of the population on the arcsinh scale, one per channel.
    """

    name: str = Field(min_length=1)
    weight: Share
    means: list[float]
    sds: list[float]

    @field_validator("sds")
    @classmethod
    def check_spread(cls, values: list[float]) -> list[float]:
        """Check that no spread is negative.

        Args:
            values: The spreads.

        Returns:
            The unchanged spreads.

        Raises:
            ValueError: If a spread is negative.
        """
        if any(value < 0 for value in values):
            raise ValueError("a spread must not be negative")
        return values


class FcsDataset(DatasetBase):
    """A folder of FCS files.

    Attributes:
        format: ``fcs``.
        file: Output folder.
        samples: Number of files in the resample mode.
        sample_pattern: Shape of a sample identifier, which is also the file name.
        events: Quantiles of the event count per file.
        channels: The channels in order.
        cofactor: Cofactor of the arcsinh scale.
        populations: The populations.
        weight_cv: Coefficient of variation of a population share between files.
        keywords: Names of the other keywords. Each gets a generic value.
    """

    format: Literal["fcs"]
    samples: int | None = Field(default=None, ge=1)
    sample_pattern: str = "SMPL-99"
    events: list[float] = Field(min_length=2)
    channels: list[FcsChannel] = Field(min_length=1)
    cofactor: float = Field(default=150.0, gt=0.0)
    populations: list[FcsPopulation] = Field(min_length=1)
    weight_cv: float = Field(default=0.3, ge=0.0)
    keywords: list[str] = Field(default_factory=list)

    check_events = field_validator("events")(tabular.check_non_decreasing)

    @model_validator(mode="after")
    def check_populations(self) -> FcsDataset:
        """Check that each population has one centre and one spread per channel.

        Returns:
            The unchanged dataset.

        Raises:
            ValueError: If a population has the wrong length or a name repeats.
        """
        for index, population in enumerate(self.populations):
            if len(population.means) != len(self.channels) or len(population.sds) != len(self.channels):
                raise ValueError(f"populations[{index}]: give one mean and one sd per channel "
                                 f"({len(self.channels)})")
        names = [population.name for population in self.populations]
        if len(names) != len(set(names)):
            raise ValueError("populations: names must be unique")
        return self

    def feature_names(self) -> set[str]:
        """Return the populations, which effects can target.

        Returns:
            The population names.
        """
        return {population.name for population in self.populations}

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


DATASET_MODELS = (FcsDataset,)


def read_fcs(path: Path, only_text: bool = False) -> Any:
    """Read one FCS file.

    The reader accepts the two offset faults that instruments write: a HEADER
    offset that disagrees with ``$BEGINDATA``, and a data end that is one byte
    too far, as the MACSQuant writes it.

    Args:
        path: The file.
        only_text: Read the keywords only, not the events.

    Returns:
        The ``flowio.FlowData`` object.

    Raises:
        ValueError: If the file is not an FCS file that ``flowio`` can read,
            such as a file with several data sets or with 24-bit integers.
    """
    import flowio

    try:
        data = flowio.FlowData(path, only_text=only_text, ignore_offset_discrepancy=True,
                               ignore_offset_error=True)
    except (flowio.exceptions.FlowIOException, ValueError) as error:
        raise ValueError(f"{path.name}: not a readable FCS file: {error}") from error
    if not only_text and data.events is None:
        widths = sorted({str(data.text.get(f"p{index}b")) for index in range(1, data.channel_count + 1)})
        raise ValueError(f"{path.name}: flowio cannot read integer data of bit width {', '.join(widths)}")
    return data


def population_shares(dataset: FcsDataset, context: Context) -> tuple[list[str], np.ndarray]:
    """Return the sample names and the population shares of each file.

    Args:
        dataset: The dataset.
        context: The generation context, which collects the truth rows.

    Returns:
        The sample names and the shares, files by populations, each row summing to 1.
    """
    config = context.config
    weights = np.array([max(population.weight, 1e-6) for population in dataset.populations])
    if config.mode == "model":
        features = [Feature(population.name, lognormal_quantiles(weight, dataset.weight_cv))
                    for population, weight in zip(dataset.populations, weights, strict=True)]
        simulation = simulate(config, dataset.name, features)
        context.add_truth("effects", simulation.effects)
        samples = sample_names([(row.sample.sample_id, row.replicate) for row in simulation.rows],
                               replicate_count(config, dataset.name))
        shares = simulation.values
    else:
        samples = invented_identifiers(dataset.sample_pattern, dataset.samples,
                                       stream(config.seed, dataset.name, "samples"))
        sigma = math.sqrt(math.log(1.0 + dataset.weight_cv ** 2))
        noise = stream(config.seed, dataset.name, "shares").standard_normal((len(samples), len(weights)))
        shares = weights[None, :] * np.exp(sigma * noise - sigma ** 2 / 2.0)
    shares = np.maximum(shares, 0.0)
    return samples, shares / shares.sum(axis=1, keepdims=True)


def file_events(dataset: FcsDataset, count: int, shares: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Draw the events of one file.

    Args:
        dataset: The dataset.
        count: Number of events.
        shares: Population shares of the file.
        rng: Random stream of the file.

    Returns:
        The events, events by channels, on the linear scale.
    """
    sizes = rng.multinomial(count, shares)
    blocks = [rng.normal(population.means, population.sds, size=(size, len(dataset.channels)))
              for population, size in zip(dataset.populations, sizes, strict=True)]
    scaled = np.sinh(np.vstack(blocks)) * dataset.cofactor
    events = scaled[rng.permutation(count)]
    for index, channel in enumerate(dataset.channels):
        if TIME_CHANNEL.match(channel.name):
            events[:, index] = np.sort(rng.uniform(0.0, channel.range * 0.5, size=count))
        events[:, index] = np.clip(events[:, index], -channel.range, channel.range)
    return events.astype(np.float32)


def generate(dataset: FcsDataset, context: Context) -> list[Path]:
    """Write one FCS file per sample.

    Args:
        dataset: The dataset.
        context: The generation context.

    Returns:
        The files written.
    """
    import flowio

    seed = context.config.seed
    samples, shares = population_shares(dataset, context)
    counts = quantile_draws(dataset.events, stream(seed, dataset.name, "events").random(len(samples)))
    folder = context.out_dir / dataset.file
    folder.mkdir(parents=True, exist_ok=True)
    names = [channel.name for channel in dataset.channels]
    labels = [channel.label or "" for channel in dataset.channels]
    paths = []
    for index, sample in enumerate(samples):
        events = file_events(dataset, int(round(counts[index])), shares[index], stream(seed, dataset.name, sample))
        metadata = {keyword: "value" for keyword in dataset.keywords}
        metadata.update({f"p{number}r": str(int(channel.range)) for number, channel in enumerate(dataset.channels, 1)})
        metadata.update({"fil": f"{sample}.fcs", "date": "01-JAN-2000", "sdg_synthetic": f"sdg {SDG_VERSION}"})
        path = folder / f"{sample}.fcs"
        with path.open("wb") as handle:
            flowio.create_fcs(handle, events.ravel(), names, opt_channel_names=labels, metadata_dict=metadata)
        paths.append(path)
    return paths


def kmeans(points: np.ndarray, clusters: int, rng: np.random.Generator) -> np.ndarray:
    """Cluster points with k-means and a k-means++ start.

    Args:
        points: Points, one per row.
        clusters: Number of clusters.
        rng: Random stream of the start.

    Returns:
        The cluster of each point.
    """
    centres = [points[rng.integers(len(points))]]
    for _ in range(1, clusters):
        distances = np.min([((points - centre) ** 2).sum(axis=1) for centre in centres], axis=0)
        total = distances.sum()
        centres.append(points[rng.choice(len(points), p=distances / total)] if total > 0 else points[0])
    centres = np.array(centres)
    labels = np.zeros(len(points), dtype=int)
    for _ in range(KMEANS_ROUNDS):
        labels = np.argmin(((points[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2), axis=1)
        updated = np.array([points[labels == index].mean(axis=0) if np.any(labels == index) else centres[index]
                            for index in range(clusters)])
        if np.allclose(updated, centres):
            break
        centres = updated
    return labels


def pooled_events(files: list[Any], cofactor: float) -> tuple[np.ndarray, list[np.ndarray]]:
    """Pool the first events of each file on the arcsinh scale.

    Args:
        files: The read files.
        cofactor: Cofactor of the arcsinh scale.

    Returns:
        The pooled events and the events of each file, both on the arcsinh scale.
    """
    per_file = [np.arcsinh(data.as_array(preprocess=True)[:POOL_EVENTS] / cofactor) for data in files]
    return np.vstack(per_file), per_file


def fit_populations(files: list[Any], cofactor: float, clusters: int,
                    time_index: int | None) -> tuple[list[dict[str, Any]], float | None]:
    """Find the populations of the pooled events.

    A cluster with fewer events than ``MIN_VALUES`` is dropped, so no
    population stands for a single event.

    Args:
        files: The read files.
        cofactor: Cofactor of the arcsinh scale.
        clusters: Number of clusters.
        time_index: Position of the time channel, which takes no part.

    Returns:
        The population configs and the median CV of a population share across
        files, or None with fewer than ``MIN_VALUES`` files.
    """
    pooled, per_file = pooled_events(files, cofactor)
    used = [index for index in range(pooled.shape[1]) if index != time_index]
    labels = kmeans(pooled[:, used], min(clusters, len(pooled)), np.random.default_rng(0))
    kept = [index for index in range(labels.max() + 1) if (labels == index).sum() >= privacy.MIN_VALUES]
    populations = []
    for number, index in enumerate(kept, start=1):
        members = pooled[labels == index]
        populations.append({"name": f"population_{number}",
                            "weight": tabular.share(len(members), sum((labels == k).sum() for k in kept)),
                            "means": [privacy.round_sig(float(value)) for value in members.mean(axis=0)],
                            "sds": [privacy.round_sig(float(value)) for value in members.std(axis=0)]})
    if len(files) < privacy.MIN_VALUES:
        return populations, None
    starts = np.cumsum([0] + [len(events) for events in per_file])
    shares = np.array([[np.mean(labels[starts[file]:starts[file + 1]] == index) for index in kept]
                       for file in range(len(per_file))])
    with np.errstate(divide="ignore", invalid="ignore"):
        cvs = shares.std(axis=0, ddof=1) / shares.mean(axis=0)
    return populations, privacy.round_sig(float(np.nanmedian(cvs)))


def profile(paths: list[Path], labels: list[str], options: privacy.Options) -> list[tuple[dict[str, Any], list[str]]]:
    """Profile a set of FCS files into one dataset.

    Args:
        paths: The FCS files.
        labels: Generic label of each file.
        options: The profile options. ``--set populations=8`` sets the cluster count.

    Returns:
        One dataset config, with the paths of its default fields.
    """
    files = [read_fcs(path) for path in paths]
    first = files[0]
    channels = [{"name": name, "label": label or None, "range": float(value)}
                for name, label, value in zip(first.pnn_labels, first.pns_labels, first.pnr_values, strict=True)]
    if options.generic_labels:
        for number, channel in enumerate(channels, start=1):
            channel["label"] = f"marker_{number}" if channel["label"] else None
    time_index = next((index for index, channel in enumerate(channels) if TIME_CHANNEL.match(channel["name"])), None)
    cofactor = float(options.extra.get("cofactor", 150.0))
    populations, weight_cv = fit_populations(files, cofactor, int(options.extra.get("populations",
                                                                                    DEFAULT_POPULATIONS)), time_index)
    counts = [float(data.event_count) for data in files]
    defaults = []
    if len(files) >= privacy.MIN_VALUES:
        events = tabular.rounded_quantiles(counts)
    else:
        events = list(DEFAULT_EVENTS)
        defaults.append("events")
    keywords = sorted({key for data in files for key in data.text if not LAYOUT_KEYWORDS.match(key)}
                      - {"fil", "date", "sdg_synthetic"})
    sample_ids = [path.stem for path in paths]
    dataset: dict[str, Any] = {"name": "fcs", "format": "fcs", "file": "fcs", "samples": len(files),
                               "sample_pattern": most_common_shape(sample_ids), "events": events,
                               "channels": [{key: value for key, value in channel.items() if value is not None}
                                            for channel in channels],
                               "cofactor": cofactor, "populations": populations, "keywords": keywords}
    if weight_cv is not None:
        dataset["weight_cv"] = weight_cv
    return [(dataset, defaults)]


def glimpse(paths: list[Path], labels: list[str], options: privacy.Options) -> list[TableReport]:
    """Report a set of FCS files as one table, one column per channel.

    Args:
        paths: The FCS files.
        labels: Generic label of each file.
        options: The glimpse options.

    Returns:
        One table report.
    """
    files = [read_fcs(path) for path in paths]
    first = files[0]
    counts = [data.event_count for data in files]
    keywords = sorted({key for data in files for key in data.text if not LAYOUT_KEYWORDS.match(key)})
    facts = [Fact("rows", str(len(files))), Fact("channels", str(first.channel_count)),
             Fact("version", str(first.version)), Fact("datatype", str(first.text.get("datatype", ""))),
             Fact("keywords", str(len(keywords)))]
    if len(files) >= privacy.MIN_VALUES:
        facts.append(Fact("events", f"{privacy.format_number(min(counts))} to {privacy.format_number(max(counts))}"))
    label = labels[0] if len(labels) == 1 else f"{labels[0]} to {labels[-1]}"
    report = TableReport(label, "fcs", facts)
    for number, (name, marker, value) in enumerate(zip(first.pnn_labels, first.pns_labels, first.pnr_values,
                                                       strict=True), start=1):
        shown = f"marker_{number}" if options.generic_labels and marker else marker
        report.columns.append(ColumnReport(name, "channel", [Fact("label", repr(shown or "")),
                                                             Fact("range", privacy.format_number(float(value)))]))
    return [report]


def audit_values(paths: list[Path], options: privacy.Options) -> dict[str, str]:
    """Collect the keyword values, which become generic tokens.

    A value that equals a channel name, or a marker label that the checked
    text keeps, is not collected: some instruments copy them into their own
    keywords, such as ``BD$P1N``, and the report prints them by rule 3.

    Args:
        paths: The FCS files.
        options: The options that made the checked text.

    Returns:
        Each sensitive value mapped to its source kind.
    """
    found: dict[str, str] = {}
    for path in paths:
        data = read_fcs(path, only_text=True)
        public = set(data.pnn_labels) | (set() if options.generic_labels else set(data.pns_labels))
        for key, value in data.text.items():
            text = str(value).strip()
            if (not LAYOUT_KEYWORDS.match(key) and len(text) >= 3 and not re.fullmatch(r"[-\d.,]+", text)
                    and text not in public):
                found[text] = f"keyword {key}"
        if options.generic_labels:
            # Some instruments copy the channel name into $PnS, such as FSC-A
            # or HDR-T on the public EY and cyflow files; the report prints it
            # as the channel name by rule 3.
            found.update({str(label): "marker label" for label in data.pns_labels
                          if label and str(label) not in data.pnn_labels})
    return found


TEMPLATE = '''\
  # A folder of FCS files, one per sample. Events come from populations on the
  # arcsinh scale. The model mode takes the samples of the design.
  - name: {name}
    format: fcs
    file: {name}                      # the output folder
{samples}    events: [8000, 12000]             # quantiles of the event count per file
    channels:                         # $PnN, $PnS and $PnR
      - {{name: FSC-A, range: 262144}}
      - {{name: SSC-A, range: 262144}}
      - {{name: FL1-A, label: CD4, range: 262144}}
      - {{name: FL2-A, label: CD8, range: 262144}}
      - {{name: Time, range: 262144}}
    cofactor: 150
    populations:                      # share, and centre and spread per channel (arcsinh scale)
      - {{name: cd4_t, weight: 0.6, means: [7.0, 5.5, 4.5, 0.5, 0], sds: [0.3, 0.4, 0.4, 0.5, 0]}}
      - {{name: cd8_t, weight: 0.3, means: [6.8, 5.3, 0.5, 4.0, 0], sds: [0.3, 0.4, 0.5, 0.4, 0]}}
      - {{name: debris, weight: 0.1, means: [4.0, 3.0, 0.3, 0.3, 0], sds: [0.8, 0.8, 0.6, 0.6, 0]}}
    weight_cv: 0.3                    # spread of a population share between files
    keywords: [CYT, OP]               # other keyword names; each value is generic
'''


def template(fmt: str, mode: str, name: str) -> tuple[str, str | None]:
    """Return the commented template of an FCS dataset.

    Args:
        fmt: ``fcs``.
        mode: ``resample`` or ``model``.
        name: Dataset name.

    Returns:
        The YAML list item, and the feature that a template effect can target.
    """
    samples = "" if mode == "model" else "    samples: 6\n    sample_pattern: SMPL-99\n"
    return TEMPLATE.format(name=name, samples=samples), "cd8_t" if mode == "model" else None


def check_output(dataset: FcsDataset, config: Any, out_dir: Path) -> list[str]:
    """Compare the FCS files with the config: file count and channels.

    Args:
        dataset: The dataset config.
        config: The whole config.
        out_dir: The output folder.

    Returns:
        The problems found.
    """
    files = sorted((out_dir / dataset.file).glob("*.fcs"))
    expected = sample_count(dataset, config)
    issues = []
    if len(files) != expected:
        issues.append(f"{dataset.file}: {len(files)} FCS files, the config gives {expected}")
    if files and read_fcs(files[0], only_text=True).pnn_labels != [channel.name for channel in dataset.channels]:
        issues.append(f"{dataset.file}: the channels of {files[0].name} differ from the config")
    return issues


def classified_channels(dataset: FcsDataset) -> list[int]:
    """Return the channels that separate the populations.

    The time channel and a channel with no spread in any population carry no
    population signal, so the classification leaves them out.

    Args:
        dataset: The dataset config.

    Returns:
        The positions of the channels to use.
    """
    return [index for index, channel in enumerate(dataset.channels)
            if not TIME_CHANNEL.match(channel.name)
            and any(population.sds[index] > 0 for population in dataset.populations)]


def population_frequencies(dataset: FcsDataset, events: np.ndarray, used: list[int]) -> np.ndarray:
    """Assign each event to a population and return the population shares.

    Each event goes to the population with the highest Gaussian likelihood on
    the arcsinh scale, from the centres and spreads of the config. The weights
    of the config are not used as priors, so a changed share is not pulled back
    to the config value.

    Args:
        dataset: The dataset config.
        events: Events of one file, events by channels, on the linear scale.
        used: Positions of the channels to use.

    Returns:
        The share of the events in each population, in config order.
    """
    scaled = np.arcsinh(events[:, used] / dataset.cofactor)
    means = np.array([[population.means[index] for index in used] for population in dataset.populations])
    sds = np.maximum(np.array([[population.sds[index] for index in used] for population in dataset.populations]),
                     MIN_SPREAD)
    distances = ((scaled[:, None, :] - means[None, :, :]) / sds[None, :, :]) ** 2
    likelihood = -0.5 * distances.sum(axis=2) - np.log(sds).sum(axis=1)[None, :]
    labels = np.argmax(likelihood, axis=1)
    return np.bincount(labels, minlength=len(dataset.populations)) / max(len(labels), 1)


def observed(dataset: FcsDataset, out_dir: Path) -> dict[tuple[str, str], list[float]]:
    """Read the population frequencies back, by sample and population.

    Args:
        dataset: The dataset config.
        out_dir: The output folder.

    Returns:
        The frequency of each sample and population.
    """
    used = classified_channels(dataset)
    found: dict[tuple[str, str], list[float]] = {}
    for path in sorted((out_dir / dataset.file).glob("*.fcs")):
        shares = population_frequencies(dataset, read_fcs(path).as_array(preprocess=True), used)
        for population, share in zip(dataset.populations, shares, strict=True):
            found.setdefault((strip_replicate(path.stem), population.name), []).append(float(share))
    return found
