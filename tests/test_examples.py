"""Tests of the examples, the init templates and the agent harness files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from sdg.config import load_config, parse_config
from sdg.formats import MODULES
from sdg.generate import generate
from sdg.templates import init_config

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = sorted((ROOT / "example" / "configs").glob("*.yaml"))
DESCRIPTIONS = sorted((ROOT / "example" / "descriptions").glob("*.md"))
FORMATS = [name for module in MODULES for name in module.FORMATS]


@pytest.mark.parametrize("config", CONFIGS, ids=[path.stem for path in CONFIGS])
def test_example_configs_generate(config: Path, tmp_path: Path) -> None:
    """Each example config validates and generates."""
    load_config(config)
    assert generate(config, tmp_path / "out")


@pytest.mark.parametrize("description", DESCRIPTIONS, ids=[path.stem for path in DESCRIPTIONS])
def test_each_description_has_a_config(description: Path) -> None:
    """Each description has the config that the workflow made from it."""
    assert (ROOT / "example" / "configs" / f"{description.stem}.yaml").is_file()


@pytest.mark.parametrize("mode", ["resample", "model"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_init_templates_validate(fmt: str, mode: str) -> None:
    """The template of each format validates in both modes."""
    parse_config(yaml.safe_load(init_config([fmt], mode)))


def test_init_template_generates(tmp_path: Path) -> None:
    """A template with every format generates as it is."""
    path = tmp_path / "config.yaml"
    path.write_text(init_config(FORMATS), encoding="utf-8")
    assert generate(path, tmp_path / "out")


def test_harness_files_are_in_sync() -> None:
    """The copies and the adapters match their sources, and no symlink exists."""
    completed = subprocess.run([str(ROOT / "scripts" / "sync_agent_harness.sh"), "--check"],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_skill_frontmatter() -> None:
    """The skill names itself sdg and says when to use it."""
    text = (ROOT / "skills" / "claude-code" / "SKILL.md").read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    assert front["name"] == "sdg"
    assert "synthetic" in front["description"]
