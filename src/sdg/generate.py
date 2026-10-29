"""``sdg generate``: write synthetic data from a config.

The driver loads the config, asks each format module to write its datasets,
writes the truth tables that the modules collect, and finishes the output
folder with the config copy, the notice and the manifest.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdg.config import Config, load_config
from sdg.design import sample_truth
from sdg.formats import module_for_format
from sdg.output import MANIFEST_NAME, copy_config, write_manifest, write_notice

TRUTH_DIR = "truth"


@dataclass
class Context:
    """What a format module needs to write one dataset.

    Attributes:
        config: The whole config.
        out_dir: The output folder.
        truth: Truth rows that the modules add, by table name.
    """

    config: Config
    out_dir: Path
    truth: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def add_truth(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Add rows to a truth table.

        Args:
            table: Truth table name, such as ``effects``.
            rows: The rows, each a mapping of column to value.
        """
        self.truth.setdefault(table, []).extend(rows)


def check_out_dir(out_dir: Path) -> None:
    """Refuse to write into a folder that sdg did not make.

    An existing folder is reused only when it holds an sdg manifest, so a
    mistyped path never overwrites real data.

    Args:
        out_dir: The output folder.

    Raises:
        FileExistsError: If the folder holds files and no sdg manifest.
    """
    if out_dir.is_dir() and any(out_dir.iterdir()) and not (out_dir / MANIFEST_NAME).is_file():
        raise FileExistsError(f"{out_dir} holds files that sdg did not write; choose an empty folder")


def write_truth(context: Context) -> list[Path]:
    """Write each truth table as a CSV file under ``truth/``.

    Args:
        context: The generation context with its truth rows.

    Returns:
        The truth files.
    """
    paths = []
    for table, rows in sorted(context.truth.items()):
        if not rows:
            continue
        path = context.out_dir / TRUTH_DIR / f"{table}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = list(dict.fromkeys(key for row in rows for key in row))
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        paths.append(path)
    return paths


def generate(config_path: Path, out_dir: Path) -> list[Path]:
    """Write every dataset of a config into an output folder.

    Args:
        config_path: The YAML config.
        out_dir: The output folder.

    Returns:
        Every file written, the manifest last.

    Raises:
        ConfigError: If the config does not validate.
        FileExistsError: If the folder holds files that sdg did not write.
    """
    config = load_config(config_path)
    check_out_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    context = Context(config, out_dir)
    if config.mode == "model":
        context.add_truth("samples", sample_truth(config))
    files: list[Path] = []
    for dataset in config.datasets:
        files += module_for_format(dataset.format).generate(dataset, context)
    files += write_truth(context)
    files += [copy_config(config_path, out_dir), write_notice(out_dir)]
    return files + [write_manifest(out_dir, config, files)]
