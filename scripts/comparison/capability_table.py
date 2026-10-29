"""Join the capability results of the three tools into one table.

Each tool script writes ``<tool>.tsv`` with a result per checked capability.
A capability with no row for a tool has no function in that tool, and the
table marks it ``none``. Only ``pass`` counts.

Usage:
    uv run python scripts/comparison/capability_table.py RUN_DIR

Writes ``RUN_DIR/capabilities.md`` and ``RUN_DIR/capabilities.tsv``.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

TOOLS = {"sdg": "sdg", "fakedatar": "FakeDataR", "faker": "Faker"}
CAPABILITIES = Path("scripts/comparison/capabilities.txt")


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a tab-separated file with a header.

    Args:
        path: The file.

    Returns:
        One mapping per row.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def results(run_dir: Path) -> dict[str, dict[str, tuple[str, str]]]:
    """Read the result of each tool.

    Args:
        run_dir: The run folder.

    Returns:
        Each tool mapped to each capability id and its result and note.
    """
    found = {}
    for tool in TOOLS:
        path = run_dir / f"{tool}.tsv"
        rows = read_rows(path) if path.exists() else []
        found[tool] = {row["id"]: (row["result"], row["note"]) for row in rows}
    return found


def markdown(capabilities: list[dict[str, str]], found: dict[str, dict[str, tuple[str, str]]]) -> str:
    """Write the capability table and the counts as Markdown.

    Args:
        capabilities: The rows of ``capabilities.txt``.
        found: The results of each tool.

    Returns:
        The Markdown text.
    """
    lines = ["| Id | Capability | " + " | ".join(TOOLS.values()) + " |", "|---|---|" + "---|" * len(TOOLS)]
    for row in capabilities:
        cells = [found[tool].get(row["id"], ("none", ""))[0] for tool in TOOLS]
        lines.append(f"| {row['id']} | {row['capability']} | " + " | ".join(cells) + " |")
    lines += ["", "| Group | " + " | ".join(TOOLS.values()) + " |", "|---|" + "---|" * len(TOOLS)]
    groups = list(dict.fromkeys(row["group"] for row in capabilities))
    for group in [*groups, "Total", "Total without the domain formats"]:
        members = [row for row in capabilities
                   if row["group"] == group or group == "Total"
                   or (group.startswith("Total without") and row["group"] != "Formats")]
        counts = [sum(found[tool].get(row["id"], ("none", ""))[0] == "pass" for row in members) for tool in TOOLS]
        lines.append(f"| {group} | " + " | ".join(f"{count} of {len(members)}" for count in counts) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Write the joined table.

    Returns:
        The exit code.
    """
    run_dir = Path(sys.argv[1])
    capabilities = read_rows(CAPABILITIES)
    found = results(run_dir)
    (run_dir / "capabilities.md").write_text(markdown(capabilities, found), encoding="utf-8")
    with (run_dir / "capabilities.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["id", "group", "capability", *[f"{tool}_{part}" for tool in TOOLS
                                                       for part in ("result", "note")]])
        for row in capabilities:
            cells = [item for tool in TOOLS for item in found[tool].get(row["id"], ("none", ""))]
            writer.writerow([row["id"], row["group"], row["capability"], *cells])
    print(f"Wrote {run_dir / 'capabilities.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
