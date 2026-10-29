"""``sdg profile``: write a config from real files, under the privacy rules.

The profile runs on the secure machine. It writes a config that holds the
structure and rounded summaries of the real data and no real value. Every value
that the privacy rules forbid becomes a default, and the header of the config
lists each default so a person can edit it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sdg import privacy
from sdg.config import SCHEMA_VERSION, parse_config
from sdg.formats import detect_module
from sdg.glimpse import expand_targets
from sdg.version import SDG_VERSION


@dataclass
class Profile:
    """A config written from real files.

    Attributes:
        config: The config as plain data, ready for YAML.
        defaults: Paths of the fields that hold defaults, not measurements.
        mapping: Each generic file label and the real file that it stands for.
            The mapping never goes into the config.
    """

    config: dict[str, Any]
    defaults: list[str] = field(default_factory=list)
    mapping: list[tuple[str, Path]] = field(default_factory=list)


def profile(targets: list[Path], options: privacy.Options, seed: int = 0) -> Profile:
    """Profile every file under the targets into one config.

    Args:
        targets: Files or directories.
        options: The profile options.
        seed: Seed to write into the config.

    Returns:
        The profile. Its config validates.
    """
    files = expand_targets(targets, options.recursive)
    groups: dict[str, tuple] = {}
    mapping = []
    for index, path in enumerate(files, start=1):
        module = detect_module(path, options.format)
        label = privacy.generic_file_label(index, path)
        paths, labels = groups.setdefault(module.__name__, (module, [], []))[1:]
        paths.append(path)
        labels.append(label)
        mapping.append((label, path))
        privacy.note_key(options, "file", "", None, "", {path: label})
    datasets, defaults = [], []
    for module, paths, labels in groups.values():
        for dataset, defaulted in module.profile(paths, labels, options):
            defaults += [f"datasets[{len(datasets)}].{item}" for item in defaulted]
            datasets.append(dataset)
    config = {"version": SCHEMA_VERSION, "seed": seed, "mode": "resample", "datasets": datasets}
    parse_config(config)
    return Profile(config, defaults, mapping)


def to_yaml(result: Profile) -> str:
    """Write a profile as a commented YAML config.

    Args:
        result: The profile.

    Returns:
        The YAML text, with a header that lists the default fields.
    """
    lines = [f"# Written by sdg profile {SDG_VERSION} on {dt.date.today().isoformat()}.",
             "# Read this file before it leaves the secure machine."]
    if result.defaults:
        lines.append("# These fields hold defaults, not measurements. Edit them if needed:")
        lines += [f"#   {item}" for item in result.defaults]
    body = yaml.safe_dump(result.config, sort_keys=False, allow_unicode=True, default_flow_style=None,
                          width=100)
    return "\n".join(lines) + "\n" + body
