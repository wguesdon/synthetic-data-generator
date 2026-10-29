"""The fixed files of every output folder: config copy, manifest and notice.

``sdg generate`` writes the data files, then calls these functions so every
output folder carries the config that made it, a hash of each file and a
notice that the data is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from sdg.config import Config
from sdg.version import SDG_VERSION

CONFIG_NAME = "config.yaml"
MANIFEST_NAME = "manifest.json"
NOTICE_NAME = "SYNTHETIC_DATA.md"

NOTICE = """# Synthetic data

This folder holds synthetic data that `sdg` made from `config.yaml`. The values have no meaning. Do not use them for a result, a report or a publication.

`manifest.json` gives the tool version, the seed and a SHA-256 hash of each file.
"""


def file_sha256(path: Path) -> str:
    """Hash a file in chunks, so a large FASTQ file does not fill memory.

    Args:
        path: The file to hash.

    Returns:
        The hexadecimal SHA-256 digest.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def copy_config(source: Path, out_dir: Path) -> Path:
    """Copy the config file into the output folder, comments included.

    The comments matter: an agent marks each assumed value with one.

    Args:
        source: The config file that ``sdg generate`` read.
        out_dir: The output folder.

    Returns:
        The path of the copy.
    """
    target = out_dir / CONFIG_NAME
    shutil.copyfile(source, target)
    return target


def write_notice(out_dir: Path) -> Path:
    """Write the notice that the folder holds synthetic data.

    Args:
        out_dir: The output folder.

    Returns:
        The path of the notice.
    """
    target = out_dir / NOTICE_NAME
    target.write_text(NOTICE, encoding="utf-8")
    return target


def write_manifest(out_dir: Path, config: Config, files: list[Path]) -> Path:
    """Write the manifest with the tool version, the seed and each file hash.

    The manifest holds no timestamp, so two runs of the same config write the
    same manifest.

    Args:
        out_dir: The output folder.
        config: The config that made the files.
        files: The files to list, inside ``out_dir``.

    Returns:
        The path of the manifest.
    """
    entries = [{"path": path.relative_to(out_dir).as_posix(), "sha256": file_sha256(path)}
               for path in sorted(files)]
    manifest = {"tool": "sdg", "version": SDG_VERSION, "seed": config.seed, "files": entries}
    target = out_dir / MANIFEST_NAME
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target
