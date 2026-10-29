"""Version of the sdg package.

This is the single source of the version. ``pyproject.toml`` reads it through
hatchling, and ``tests/test_version.py`` checks that the root ``VERSION`` file
holds the same value.
"""

SDG_VERSION = "0.14.0"
