"""The study design as samples: subjects, groups, timepoints and replicates.

Every model-mode dataset draws from the same subjects, so a clinical table, an
Olink file and a set of FCS files share one subject list, so a join of two
assays finds each subject.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sdg.seeding import stream

if TYPE_CHECKING:
    from sdg.config import Config, Design

#: Digits of the number in a subject identifier, at least.
SUBJECT_DIGITS = 3


@dataclass(frozen=True)
class Sample:
    """One sample of the design: a subject at a timepoint.

    Attributes:
        sample_id: Sample identifier, the subject and the timepoint.
        subject: Subject identifier.
        group: Group of the subject.
        timepoint: Timepoint label, or None when the design has none.
        subject_index: Position of the subject in the subject list.
        present: False when the sample is absent from the data.
    """

    sample_id: str
    subject: str
    group: str
    timepoint: str | None
    subject_index: int
    present: bool = True


@dataclass(frozen=True)
class DesignRow:
    """One measured row: a present sample and its replicate number.

    Attributes:
        sample: The sample.
        replicate: Replicate number, from 1.
        position: Position of the sample in the full sample list, used to find
            its random draws.
    """

    sample: Sample
    replicate: int
    position: int


def subjects(design: Design) -> list[tuple[str, str]]:
    """List the subjects and their groups, group by group.

    Args:
        design: The study design.

    Returns:
        Each subject identifier with its group.
    """
    total = sum(design.subjects.groups.values())
    digits = max(SUBJECT_DIGITS, len(str(total)))
    found = []
    for group, count in design.subjects.groups.items():
        for _ in range(count):
            found.append((f"{design.subjects.prefix}{len(found) + 1:0{digits}d}", group))
    return found


def all_samples(config: Config) -> list[Sample]:
    """List every sample of the design, the absent ones included.

    The absent samples are drawn once for the whole config, so every dataset
    lacks the same samples. The first timepoint is never absent.

    Args:
        config: The config. Its design must be set.

    Returns:
        The samples, subject by subject and then timepoint by timepoint.
    """
    design = config.design
    timepoints: list[str | None] = list(design.timepoints) or [None]
    subject_list = subjects(design)
    rng = stream(config.seed, "design", "missing_samples")
    draws = rng.random(len(subject_list) * len(timepoints))
    samples = []
    for index, (subject, group) in enumerate(subject_list):
        for position, timepoint in enumerate(timepoints):
            sample_id = subject if timepoint is None else f"{subject}_{timepoint}"
            absent = position > 0 and draws[index * len(timepoints) + position] < design.missing_samples
            samples.append(Sample(sample_id, subject, group, timepoint, index, not absent))
    return samples


def design_rows(config: Config, replicates: int = 1) -> list[DesignRow]:
    """List the measured rows: each present sample, once per replicate.

    Args:
        config: The config. Its design must be set.
        replicates: Replicates of each sample.

    Returns:
        The rows in sample order.
    """
    return [DesignRow(sample, replicate, position)
            for position, sample in enumerate(all_samples(config)) if sample.present
            for replicate in range(1, replicates + 1)]


def sample_truth(config: Config) -> list[dict[str, object]]:
    """Return the sample table for ``truth/samples.csv``.

    Args:
        config: The config. Its design must be set.

    Returns:
        One row per sample, with its subject, group, timepoint and presence.
    """
    return [{"sample_id": sample.sample_id, "subject": sample.subject, "group": sample.group,
             "timepoint": sample.timepoint or "", "present": sample.present}
            for sample in all_samples(config)]
