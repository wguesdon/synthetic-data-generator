"""Write ``single_script/sdg_secure.py``, the whole sdg package in one file.

The single script is for a machine where sdg is not installed, such as a cloud
notebook on the secure side. The user uploads the one file and runs it with uv,
which reads the dependencies from the inline script metadata (PEP 723)::

    uv run sdg_secure.py glimpse data.xlsx

The script holds the source of each module of ``src/sdg`` as a table of string
literals, one literal per source line, so a diff of the script reads like a
diff of the package. An import hook loads ``sdg`` from that table, so each sdg
command and each Python function works as in the installed package.

Usage::

    uv run python scripts/build_single_script.py           # write the script
    uv run python scripts/build_single_script.py --check   # exit 1 when stale
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "sdg"
TARGET = ROOT / "single_script" / "sdg_secure.py"
VERSION_PATTERN = re.compile(r"SDG_VERSION = ['\"](?P<version>[^'\"]+)['\"]")

#: The part of the script above the source table. ``{version}``,
#: ``{requires_python}`` and ``{dependencies}`` are filled in from the package.
HEAD = '''#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "{requires_python}"
# dependencies = [
{dependencies}
# ]
# ///
"""sdg {version} in one file, for a machine where sdg is not installed.

Upload this file and run it with uv. uv reads the dependencies above and
installs them in a cached environment, so no other install step is necessary::

    uv run sdg_secure.py glimpse data.xlsx
    uv run sdg_secure.py describe design data.csv
    uv run sdg_secure.py describe groups data.csv

Each sdg command works, with the options of ``sdg --help``. The reports are
the same as those of the installed package.

To use the Python functions, import this file once. ``import sdg`` then loads
the package from the table below::

    import sdg_secure
    from sdg import describe
    print(describe.glimpse(frame, name="data.csv"))

``scripts/build_single_script.py`` writes this file from ``src/sdg`` in the
sdg repository. Do not edit it by hand.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types

#: The prefix of the file name of each bundled module, as tracebacks show it.
BUNDLE = "<sdg_secure>"


class BundleImporter(importlib.abc.MetaPathFinder, importlib.abc.InspectLoader):
    """Find and load the sdg modules from :data:`SOURCES`."""

    def find_spec(self, fullname: str, path: object = None,
                  target: object = None) -> importlib.machinery.ModuleSpec | None:
        """Return the spec of a bundled module.

        Args:
            fullname: The full module name, such as ``sdg.formats.tabular``.
            path: The search path of the parent package. Not used.
            target: The module that a reload replaces. Not used.

        Returns:
            The module spec, or None when the module is not in the bundle.
        """
        key = source_key(fullname)
        if key is None:
            return None
        return importlib.machinery.ModuleSpec(fullname, self, origin=f"{{BUNDLE}}/{{key}}",
                                              is_package=key.endswith("/__init__.py"))

    def is_package(self, fullname: str) -> bool:
        """Say whether a bundled module is a package.

        Args:
            fullname: The full module name.

        Returns:
            True when the module comes from an ``__init__.py`` source.

        Raises:
            ImportError: If the module is not in the bundle.
        """
        return bundled_key(fullname).endswith("/__init__.py")

    def get_source(self, fullname: str) -> str:
        """Return the source of a bundled module, for tracebacks.

        Args:
            fullname: The full module name.

        Returns:
            The source text.

        Raises:
            ImportError: If the module is not in the bundle.
        """
        return SOURCES[bundled_key(fullname)]

    def get_code(self, fullname: str) -> types.CodeType:
        """Compile a bundled module.

        The code keeps the bundle file name of the module, so a traceback
        names the module and shows its lines. ``dont_inherit`` stops the
        future imports of this file from reaching the module.

        Args:
            fullname: The full module name.

        Returns:
            The code object of the module.

        Raises:
            ImportError: If the module is not in the bundle.
        """
        key = bundled_key(fullname)
        return compile(SOURCES[key], f"{{BUNDLE}}/{{key}}", "exec", dont_inherit=True)


def source_key(fullname: str) -> str | None:
    """Return the table key of a module name.

    Args:
        fullname: The full module name, such as ``sdg.formats``.

    Returns:
        The key, such as ``sdg/formats/__init__.py``, or None when the module
        is not in the bundle.
    """
    path = fullname.replace(".", "/")
    for key in (f"{{path}}/__init__.py", f"{{path}}.py"):
        if key in SOURCES:
            return key
    return None


def bundled_key(fullname: str) -> str:
    """Return the table key of a module that must be in the bundle.

    Args:
        fullname: The full module name.

    Returns:
        The key in :data:`SOURCES`.

    Raises:
        ImportError: If the module is not in the bundle.
    """
    key = source_key(fullname)
    if key is None:
        raise ImportError(f"{{fullname}} is not in the sdg single script", name=fullname)
    return key


def install() -> None:
    """Put the bundle importer first on the import path, one time only."""
    if not any(isinstance(finder, BundleImporter) for finder in sys.meta_path):
        sys.meta_path.insert(0, BundleImporter())


def run() -> int:
    """Run the sdg command line.

    Returns:
        The exit status of the command.
    """
    from sdg.cli import main

    return main()


#: The source of each module of the sdg package, by its path in the package.
SOURCES = {{
'''

#: The part of the script below the source table.
TAIL = '''}}

install()

if __name__ == "__main__":
    sys.exit(run())
'''


def dependency_lines(project: dict) -> str:
    """Write the dependencies of the package as script metadata lines.

    Args:
        project: The ``[project]`` table of ``pyproject.toml``.

    Returns:
        One commented line per dependency.
    """
    return "\n".join(f'#     "{requirement}",' for requirement in project["dependencies"])


def source_entry(key: str, text: str) -> str:
    """Write one module of the source table.

    Args:
        key: The path of the module in the package, such as ``sdg/cli.py``.
        text: The source of the module.

    Returns:
        The table entry, with one string literal per source line.

    Raises:
        ValueError: If the literals do not give back the source.
    """
    lines = text.splitlines(keepends=True)
    if "".join(lines) != text:
        raise ValueError(f"{key}: the source lines do not join back to the source")
    literals = "".join(f"        {line!r}\n" for line in lines) or "        ''\n"
    return f"    {key!r}: (\n{literals}    ),\n"


def build() -> str:
    """Build the text of the single script from the package source.

    Returns:
        The text of ``single_script/sdg_secure.py``.

    Raises:
        ValueError: If the version or a source line cannot be read.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    match = VERSION_PATTERN.search((PACKAGE / "version.py").read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("src/sdg/version.py has no SDG_VERSION")
    head = HEAD.format(version=match["version"], requires_python=project["requires-python"],
                       dependencies=dependency_lines(project))
    paths = sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)
    entries = [source_entry(path.relative_to(PACKAGE.parent).as_posix(), path.read_text(encoding="utf-8"))
               for path in paths]
    return head + "".join(entries) + TAIL.format()


def main(argv: list[str] | None = None) -> int:
    """Write the single script, or check that it is up to date.

    Args:
        argv: The command line arguments. The default reads ``sys.argv``.

    Returns:
        0 on success, 1 when ``--check`` finds a stale or missing script.
    """
    parser = argparse.ArgumentParser(description="Write single_script/sdg_secure.py from src/sdg.")
    parser.add_argument("--check", action="store_true", help="Exit 1 when the script differs from the source.")
    args = parser.parse_args(argv)
    text = build()
    relative = TARGET.relative_to(ROOT)
    if args.check:
        if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != text:
            print(f"{relative} differs from src/sdg. Run: uv run python scripts/build_single_script.py",
                  file=sys.stderr)
            return 1
        return 0
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {relative}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
