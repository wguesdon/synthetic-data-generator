"""Run the sdg command line with the private-column rule switched off.

The comparison uses this wrapper to measure the generator apart from privacy
rule 2. A table with a column such as ``age`` or ``sex`` records counts only,
so ``sdg profile`` writes default quantiles for it. Here the pattern of private
column names matches nothing. Give ``--private '^$'`` as well, so that no file
is private by its name. sdg 0.8.0 has no option for this; the wrapper is a
measurement tool, not a user feature.

Usage:
    uv run python scripts/comparison/sdg_rule_off.py profile data.csv --private '^$' -o config.yaml
"""

from __future__ import annotations

import re
import sys

from sdg import cli, privacy

#: A pattern that matches no column name.
NO_NAME = re.compile(r"(?!)")


def main() -> int:
    """Run one sdg command with no private column names.

    Returns:
        The exit code of the command.
    """
    privacy.PRIVATE_COLUMNS = NO_NAME
    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
