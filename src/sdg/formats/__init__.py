"""Format modules and the dataset models that they register.

Each format module supplies:

* ``FORMATS``: the format names that it handles.
* ``SUFFIXES``: the file suffixes that it reads.
* ``DATASET_MODELS``: its config models, each with a ``format`` field typed as a
  ``Literal`` of format names.
* ``glimpse(paths, labels, options)``: table reports of real files.

A module that must look inside a file to claim it, such as Olink, also supplies
``sniff(path)``. To add a format, add its module and list it in ``MODULES``,
before ``tabular`` when it sniffs a suffix that ``tabular`` also reads.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from sdg import privacy
from sdg.formats import tabular  # noqa: I001 - the other modules import tabular, so it loads first
from sdg.formats import counts, fastq, fcs, microarray, nanostring, olink

MODULES: tuple[ModuleType, ...] = (olink, tabular, counts, microarray, nanostring, fcs, fastq)

DATASET_MODELS = tuple(model for module in MODULES for model in module.DATASET_MODELS)

#: Every suffix that some module reads.
KNOWN_SUFFIXES = frozenset(suffix for module in MODULES for suffix in module.SUFFIXES)


def module_for_format(name: str) -> ModuleType:
    """Return the module that handles a format name.

    Args:
        name: A format name, such as ``csv`` or ``fastq``.

    Returns:
        The format module.

    Raises:
        ValueError: If no module handles the format.
    """
    for module in MODULES:
        if name in module.FORMATS:
            return module
    known = ", ".join(name for module in MODULES for name in module.FORMATS)
    raise ValueError(f"unknown format {name!r}; known formats: {known}")


def detect_module(path: Path, forced: str | None = None) -> ModuleType:
    """Choose the module that reads a file.

    Args:
        path: The file.
        forced: A format name that overrides the detection.

    Returns:
        The format module.

    Raises:
        ValueError: If no module reads the file suffix.
    """
    if forced:
        return module_for_format(forced)
    suffix = privacy.file_suffix(path)
    candidates = [module for module in MODULES if suffix in module.SUFFIXES]
    for module in candidates:
        sniff = getattr(module, "sniff", None)
        if sniff is None or sniff(path):
            return module
    raise ValueError(f"{path.name}: no format reads {suffix or 'a file without a suffix'}; "
                     "use --format to choose one")
