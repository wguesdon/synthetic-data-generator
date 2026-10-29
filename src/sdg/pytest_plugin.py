"""A pytest plugin that gives the tests of a project its synthetic data.

pytest loads the plugin when sdg is installed, through the ``pytest11`` entry
point. A test asks for the fixture ``sdg_data`` and calls it with the path of
a config. The call generates the data into a new temporary folder and returns
the folder, so the code under test reads the same files as on the real data::

    def test_pipeline_reads_the_panel(sdg_data):
        data = sdg_data("config.yaml")
        result = run_pipeline(data / "file_1.xlsx")
        assert not result.empty
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest


def generate_into(factory: pytest.TempPathFactory, config: str | Path, name: str | None = None) -> Path:
    """Generate a config into a new temporary folder.

    Args:
        factory: The temporary folder factory of pytest.
        config: Path of the config.
        name: Base name of the folder. None uses the config file name.

    Returns:
        The output folder, which holds the data files, the truth tables and
        the manifest.
    """
    from sdg.generate import generate

    out = factory.mktemp(name or Path(config).stem) / "data"
    generate(Path(config), out)
    return out


@pytest.fixture
def sdg_data(tmp_path_factory: pytest.TempPathFactory) -> partial[Path]:
    """Give a test a function that generates a config and returns the output folder.

    Args:
        tmp_path_factory: The temporary folder factory of pytest.

    Returns:
        The function ``sdg_data(config, name=None)``.
    """
    return partial(generate_into, tmp_path_factory)
