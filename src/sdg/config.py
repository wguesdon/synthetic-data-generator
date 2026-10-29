"""The sdg config: its schema, its loader and its error reporting.

The config is the only input of ``sdg generate``. ``sdg profile`` or an agent
writes it, and a person edits it. Every error names the path of the field that
failed, such as ``datasets[0].sheets[0].columns[2].quantiles``, so a person or
an agent can find and fix it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import Field, PositiveFloat, PositiveInt, ValidationError, field_validator, model_validator

from sdg.config_base import StrictModel
from sdg.formats import DATASET_MODELS

#: Config schema version. A breaking change to the schema increments it.
SCHEMA_VERSION = 1

Dataset = Annotated[Union[DATASET_MODELS], Field(discriminator="format")]


class Subjects(StrictModel):
    """The subjects of the study and the group of each.

    Attributes:
        prefix: Prefix of a subject identifier, which is the prefix and a
            zero-padded number, as ``SUBJ-001``.
        groups: Each group and its subject count.
    """

    prefix: str = Field(default="SUBJ-", min_length=1)
    groups: dict[str, PositiveInt] = Field(min_length=1)


class Design(StrictModel):
    """The study design that several datasets can share.

    Attributes:
        subjects: The subjects and their groups.
        timepoints: Timepoint labels in study order.
        icc: Share of the variance of a feature that lies between subjects. A
            high value makes the samples of one subject alike.
        missing_samples: Share of the samples after the first timepoint that
            are absent, as when a sample failed or a patient missed a visit.
    """

    subjects: Subjects
    timepoints: list[str] = Field(default_factory=list)
    icc: float = Field(default=0.6, ge=0.0, le=1.0)
    missing_samples: float = Field(default=0.0, ge=0.0, lt=1.0)

    @field_validator("timepoints")
    @classmethod
    def check_unique_timepoints(cls, timepoints: list[str]) -> list[str]:
        """Check that no timepoint label repeats.

        Args:
            timepoints: Timepoint labels.

        Returns:
            The unchanged labels.

        Raises:
            ValueError: If a label repeats.
        """
        if len(timepoints) != len(set(timepoints)):
            raise ValueError("timepoint labels must be unique")
        return timepoints


class Effect(StrictModel):
    """A known effect that the generator plants and writes to a truth table.

    Attributes:
        dataset: Name of the dataset that holds the feature.
        feature: Name of the feature that the effect moves.
        contrast: The two groups compared, the moved group first.
        fold_change: Ratio of the first group to the second. 1.0 is no effect.
        timepoints: Timepoints where the effect applies. Empty applies it at
            every timepoint.
    """

    dataset: str
    feature: str
    contrast: tuple[str, str]
    fold_change: PositiveFloat
    timepoints: list[str] = Field(default_factory=list)


class Replicates(StrictModel):
    """Replicate measurements of each sample, such as wells on a plate.

    Attributes:
        n: Number of replicates of each sample.
        cv_pct: Coefficient of variation between replicates, in percent.
    """

    n: int = Field(ge=2)
    cv_pct: PositiveFloat


class Artefacts(StrictModel):
    """Assay artefacts that the generator adds to the values.

    Attributes:
        lower_limit_quantile: Share of the values censored at a lower limit of
            quantification. 0 censors nothing.
        replicates: Replicate measurements, or ``None`` for one value per sample.
        censor_token: Text written for a censored value, such as ``"<LLOQ"``.
            ``None`` writes the limit itself, as many assays do.
        datasets: Names of the datasets that the artefacts apply to. Empty
            applies them to every model-mode dataset.
    """

    lower_limit_quantile: float = Field(default=0.0, ge=0.0, lt=1.0)
    replicates: Replicates | None = None
    censor_token: str | None = None
    datasets: list[str] = Field(default_factory=list)


class Config(StrictModel):
    """A complete sdg config.

    Attributes:
        version: Schema version. Only ``1`` exists.
        seed: Seed of every random stream. The same seed and config give the
            same data.
        mode: ``resample`` draws each column on its own; ``model`` draws from the
            study design with effects and artefacts.
        null: True sets every effect to no effect, for a false positive check.
        design: Study design shared by the datasets.
        datasets: The datasets to generate.
        effects: Known effects to plant.
        artefacts: Assay artefacts to add.
    """

    version: Literal[1]
    seed: int = Field(ge=0)
    mode: Literal["resample", "model"] = "resample"
    null: bool = False
    design: Design | None = None
    datasets: list[Dataset] = Field(min_length=1)
    effects: list[Effect] = Field(default_factory=list)
    artefacts: Artefacts | None = None

    @model_validator(mode="after")
    def check_references(self) -> Config:
        """Check the links between sections that a field check cannot see.

        Returns:
            The unchanged config.

        Raises:
            ValueError: On the first broken link, with the path of the field.
        """
        names = [dataset.name for dataset in self.datasets]
        files = [dataset.file for dataset in self.datasets]
        if len(names) != len(set(names)):
            raise ValueError("datasets: dataset names must be unique")
        if len(files) != len(set(files)):
            raise ValueError("datasets: dataset files must be unique")
        if self.mode == "resample" and (self.effects or self.artefacts):
            raise ValueError("mode: effects and artefacts need the model mode, because the "
                             "resample mode draws each column on its own")
        if self.mode == "model" and self.design is None:
            raise ValueError("design: the model mode needs a design section")
        if self.effects and self.design is None:
            raise ValueError("design: effects need a design section to name the groups")
        for index, dataset in enumerate(self.datasets):
            error = dataset.mode_error(self.mode)
            if error:
                raise ValueError(f"datasets[{index}].{error}")
        by_name = {dataset.name: dataset for dataset in self.datasets}
        for index, dataset in enumerate(self.datasets):
            error = dataset.link_error(by_name) if hasattr(dataset, "link_error") else None
            if error:
                raise ValueError(f"datasets[{index}].{error}")
        for name in self.artefacts.datasets if self.artefacts else []:
            if name not in by_name:
                raise ValueError(f"artefacts.datasets: no dataset is named {name!r}")
        for index, effect in enumerate(self.effects):
            check_effect(index, effect, by_name, self.design)
        return self


def check_effect(index: int, effect: Effect, datasets: dict[str, Any], design: Design) -> None:
    """Check that one effect names a real dataset, feature and pair of groups.

    Args:
        index: Position of the effect in the ``effects`` list.
        effect: The effect.
        datasets: Datasets by name.
        design: The study design.

    Raises:
        ValueError: If the effect names something that the config lacks.
    """
    path = f"effects[{index}]"
    if effect.dataset not in datasets:
        raise ValueError(f"{path}.dataset: no dataset is named {effect.dataset!r}")
    if effect.feature not in datasets[effect.dataset].feature_names():
        raise ValueError(f"{path}.feature: dataset {effect.dataset!r} has no feature {effect.feature!r}")
    groups = design.subjects.groups
    for group in effect.contrast:
        if group not in groups:
            raise ValueError(f"{path}.contrast: the design has no group {group!r}")
    if effect.contrast[0] == effect.contrast[1]:
        raise ValueError(f"{path}.contrast: the two groups must differ")
    for timepoint in effect.timepoints:
        if timepoint not in design.timepoints:
            raise ValueError(f"{path}.timepoints: the design has no timepoint {timepoint!r}")


@dataclass(frozen=True)
class Issue:
    """One problem found in a config.

    Attributes:
        path: Location of the field, such as ``datasets[0].file``. Empty for a
            problem with the whole file.
        message: What is wrong.
    """

    path: str
    message: str


class ConfigError(Exception):
    """A config that cannot be read or does not validate.

    Attributes:
        issues: Every problem found.
    """

    def __init__(self, issues: list[Issue]) -> None:
        """Store the issues and build a readable message.

        Args:
            issues: Every problem found.
        """
        self.issues = issues
        super().__init__("; ".join(f"{issue.path}: {issue.message}" if issue.path else issue.message
                                   for issue in issues))


def discriminator_tags(schema: Any) -> set[str]:
    """Collect the tag values of every tagged union in a JSON schema.

    Pydantic puts the tag of a tagged union member into the error location,
    as in ``("datasets", 0, "excel", "file")``. The tags are removed from the
    printed path, so this set says which location parts are tags.

    Args:
        schema: A JSON schema, or any part of one.

    Returns:
        Every tag value found.
    """
    tags: set[str] = set()
    if isinstance(schema, dict):
        discriminator = schema.get("discriminator")
        if isinstance(discriminator, dict):
            tags.update(discriminator.get("mapping", {}))
        for value in schema.values():
            tags |= discriminator_tags(value)
    elif isinstance(schema, list):
        for item in schema:
            tags |= discriminator_tags(item)
    return tags


#: Tag values of the tagged unions in the config schema.
UNION_TAGS = frozenset(discriminator_tags(Config.model_json_schema()))


def format_location(location: tuple[Any, ...]) -> str:
    """Turn a Pydantic error location into a readable field path.

    Args:
        location: The ``loc`` of a Pydantic error.

    Returns:
        A path such as ``datasets[0].sheets[1].columns[2].quantiles``.
    """
    path = ""
    previous: Any = None
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(previous, int) and part in UNION_TAGS:
            pass
        else:
            path += f".{part}" if path else str(part)
        previous = part
    return path


def issues_from_validation(error: ValidationError) -> list[Issue]:
    """Convert a Pydantic validation error into issues.

    A model validator of :class:`Config` writes its own path at the start of
    its message, as ``effects[0].dataset: ...``, so that path is moved out of
    the message.

    Args:
        error: The validation error.

    Returns:
        One issue per Pydantic error.
    """
    issues = []
    for detail in error.errors():
        path = format_location(detail["loc"])
        message = detail["msg"].removeprefix("Value error, ")
        if not path and ": " in message:
            path, message = message.split(": ", 1)
        issues.append(Issue(path, message))
    return issues


def parse_config(data: Any) -> Config:
    """Validate parsed YAML as a config.

    Args:
        data: The parsed YAML document.

    Returns:
        The validated config.

    Raises:
        ConfigError: If the document is not a mapping or does not validate.
    """
    if not isinstance(data, dict):
        raise ConfigError([Issue("", "the config must be a YAML mapping")])
    try:
        return Config.model_validate(data)
    except ValidationError as error:
        raise ConfigError(issues_from_validation(error)) from error


def load_config(path: str | Path) -> Config:
    """Read and validate a config file.

    Args:
        path: Path of the YAML config.

    Returns:
        The validated config.

    Raises:
        ConfigError: If the file is missing, is not valid YAML or does not
            validate.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError([Issue("", f"config file not found: {path}")])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError([Issue("", f"invalid YAML: {error}")]) from error
    return parse_config(data)
