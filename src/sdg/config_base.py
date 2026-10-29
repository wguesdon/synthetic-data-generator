"""Base models that the config and every format module share.

This module sits below both ``sdg.config`` and ``sdg.formats`` so that a format
module can define its dataset model without importing the full config.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: A share between 0 and 1, such as a missing share or a level share.
Share = Annotated[float, Field(ge=0.0, le=1.0)]


def check_non_decreasing(values: list[float]) -> list[float]:
    """Check that a list of quantiles never decreases.

    Args:
        values: Quantiles at evenly spaced probabilities.

    Returns:
        The unchanged values.

    Raises:
        ValueError: If a value is smaller than the value before it.
    """
    if any(later < earlier for earlier, later in zip(values, values[1:])):
        raise ValueError("quantiles must not decrease")
    return values


class StrictModel(BaseModel):
    """A model that rejects unknown fields.

    A misspelt field in a hand-written or agent-written config must fail
    loudly rather than be dropped, or the generator silently ignores it.
    """

    model_config = ConfigDict(extra="forbid")


class NoteRow(StrictModel):
    """A row above the header or below the data, such as a title or a comment line.

    The text of such a row can name a study, a person or a date, so a config
    keeps the place of its cells only. The synthetic file writes ``note`` in
    each cell, and code that skips the rows or the comments reads it as it
    reads the real file.

    Attributes:
        cells: Count of filled cells. 0 writes a blank row.
        start: Position of the first filled cell, from 0.
        mark: Comment mark at the start of the first cell, such as ``#``.
    """

    cells: int = Field(default=1, ge=0)
    start: int = Field(default=0, ge=0)
    mark: Literal["#", "!"] | None = None


class DatasetBase(StrictModel):
    """Fields that every dataset carries, whatever its format.

    A format module subclasses this model and adds a ``format`` field typed as
    a ``Literal`` of the format names that it handles.

    Attributes:
        name: Name that effects use to refer to the dataset.
        file: Output path of the dataset, relative to the output folder.
    """

    name: str = Field(min_length=1)
    file: str = Field(min_length=1)

    @field_validator("file")
    @classmethod
    def check_file_stays_inside(cls, value: str) -> str:
        """Reject a path that would write outside the output folder.

        Args:
            value: The file path from the config.

        Returns:
            The unchanged path.

        Raises:
            ValueError: If the path is absolute or climbs with ``..``.
        """
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("file must be a relative path inside the output folder")
        return value

    def feature_names(self) -> set[str]:
        """Return the names that an effect can target in this dataset.

        Returns:
            The feature names. The base dataset has none.
        """
        return set()

    def uses_design(self) -> bool:
        """Say whether the dataset draws its rows from the study design.

        Returns:
            False for the base dataset.
        """
        return False

    def mode_error(self, mode: str) -> str | None:
        """Say why the dataset cannot be generated in a mode.

        Args:
            mode: ``resample`` or ``model``.

        Returns:
            ``field: message`` for a dataset that the mode cannot generate, or
            None.
        """
        return None

    def describe(self) -> dict[str, Any]:
        """Summarize the dataset for ``sdg check``.

        Returns:
            The name, format and file of the dataset.
        """
        return {"name": self.name, "format": getattr(self, "format", None), "file": self.file}
