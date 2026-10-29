"""``sdg quarto``: a Quarto report of the data in a folder, for the secure machine.

The report describes every table of the targets: the glimpse facts as a column
table, the first and the last rows, and a plot for each numeric and category
column. With ``--synthetic``, each real table sits beside its synthetic table,
paired by position as ``sdg compare`` pairs them, and the report lists the
differences that ``sdg compare`` finds.

The head and the tail print real values, so a report of real data must stay on
the secure machine. It carries :data:`sdg.privacy.SECURE_MARKER`, which makes
``sdg audit`` refuse it. The tables and the plots are written into the ``.qmd``
file as Markdown and inline SVG, so rendering needs Quarto only, with no Python
or R kernel.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sdg import privacy
from sdg.compare import Difference, compare
from sdg.formats.tabular import kept_table
from sdg.glimpse import expand_targets, glimpse, timestamp
from sdg.report import Report, TableReport, render, render_facts
from sdg.tables import Table, is_empty, number, read_tables
from sdg.version import SDG_VERSION

#: Formats whose tables print their rows and plots.
TABULAR_FORMATS = {"csv", "tsv", "excel", "parquet", "rds"}

#: Most columns that a head or a tail shows.
MAX_COLUMNS = 30

#: Most characters of one cell in a head or a tail.
MAX_CELL = 40

#: Bins of a histogram.
BINS = 20

#: Most bars of a category chart.
MAX_BARS = 12

#: Size of one plot, in pixels.
PLOT_WIDTH = 360
PLOT_HEIGHT = 210

#: Glimpse facts that the column table shows in columns of their own.
COUNT_FACTS = ("filled", "empty", "unique")

#: Relative widths of the columns of the column table.
COLUMN_WIDTHS = [14, 9, 5, 5, 5, 62]

#: Column kinds that get a histogram, and kinds that get a bar chart.
HISTOGRAM_KINDS = {"numeric"}
BAR_KINDS = {"categorical", "boolean"}

#: Colours of the real and the synthetic plots.
REAL_COLOUR = "#4878a8"
SYNTHETIC_COLOUR = "#d4874e"


@dataclass
class Section:
    """One table of a report: its glimpse and, for a plain table, its cells.

    Attributes:
        path: The file, or None when the glimpse label names no single file.
        report: The glimpse report of the table.
        table: The cells of a plain table, or None for another format.
    """

    path: Path | None
    report: TableReport
    table: Table | None


@dataclass
class QuartoResult:
    """The written report.

    Attributes:
        path: The ``.qmd`` file.
        secure: True when the report holds data that is not an sdg output, so it
            holds real values and must stay on the secure machine.
    """

    path: Path
    secure: bool


def is_sdg_output(target: Path) -> bool:
    """Tell whether a target is an output folder of ``sdg generate``.

    Args:
        target: A file or a folder.

    Returns:
        True for a folder with an sdg manifest.
    """
    manifest = target / "manifest.json"
    if not target.is_dir() or not manifest.is_file():
        return False
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("tool") == "sdg"
    except (ValueError, OSError):
        return False


def table_sections(targets: list[Path], options: privacy.Options) -> tuple[Report, list[Section]]:
    """Read the glimpse and the cells of every table under the targets.

    Args:
        targets: Files or folders.
        options: The reading options.

    Returns:
        The glimpse report, and one section per table in report order.
    """
    report = glimpse(targets, options)
    files = expand_targets(targets, options.recursive)
    by_label = {privacy.generic_file_label(index, path): path for index, path in enumerate(files, start=1)}
    cells: dict[Path, list[Table]] = {}
    seen: Counter = Counter()
    sections = []
    for table_report in report.tables:
        label = table_report.label.split(" | ")[0]
        path = by_label.get(label)
        position = seen[label]
        seen[label] += 1
        table = None
        if path is not None and table_report.format in TABULAR_FORMATS:
            if path not in cells:
                cells[path] = [kept_table(item, options) for item in read_tables(path)]
            table = cells[path][position] if position < len(cells[path]) else None
        sections.append(Section(path, table_report, table))
    return report, sections


def markdown_cell(value: object, limit: int | None = MAX_CELL) -> str:
    """Write one cell for a Markdown table.

    Args:
        value: The cell as read.
        limit: Most characters, or None for no cut.

    Returns:
        The text, cut to ``limit`` characters, with pipes escaped.
    """
    if is_empty(value):
        return ""
    text = " ".join(str(value).split())
    if limit is not None and len(text) > limit:
        text = text[:limit - 1] + "…"
    return text.replace("\\", "\\\\").replace("|", "\\|")


def markdown_table(header: list[str], rows: list[list[object]], limit: int | None = MAX_CELL,
                   widths: list[int] | None = None) -> list[str]:
    """Write a Markdown pipe table.

    Args:
        header: The column names.
        rows: The rows.
        limit: Most characters of a cell, or None for no cut.
        widths: The relative width of each column, or None for equal widths.

    Returns:
        The lines of the table.
    """
    dashes = widths or [3] * len(header)
    lines = ["| " + " | ".join(markdown_cell(name, limit) or " " for name in header) + " |",
             "|" + "".join("-" * width + "|" for width in dashes)]
    lines += ["| " + " | ".join(markdown_cell(cell, limit) for cell in row) + " |" for row in rows]
    return lines


def row_lines(table: Table, count: int) -> list[str]:
    """Write the head and the tail of a table.

    Args:
        table: The table.
        count: Rows of the head and of the tail.

    Returns:
        The Markdown lines. A table with at most twice ``count`` rows prints
        all of its rows once.
    """
    shown = min(len(table.header), MAX_COLUMNS)
    header = table.header[:shown]
    note = [f"The table has {len(table.header)} columns. The first {shown} are shown.", ""] \
        if len(table.header) > shown else []
    if len(table.rows) <= 2 * count:
        return note + [f"All {len(table.rows)} rows.", ""] + markdown_table(header, [row[:shown] for row in table.rows])
    head = [row[:shown] for row in table.rows[:count]]
    tail = [row[:shown] for row in table.rows[-count:]]
    return note + [f"The first {count} rows of {len(table.rows)}.", ""] + markdown_table(header, head) + \
        ["", f"The last {count} rows.", ""] + markdown_table(header, tail)


def column_lines(report: TableReport) -> list[str]:
    """Write the column table of a glimpse table report.

    Args:
        report: The table report.

    Returns:
        The Markdown lines: the table facts, then one row per column.
    """
    lines = [f"*{render_facts(report.facts)}*", ""] if report.facts else []
    rows = []
    for column in report.columns:
        facts = {fact.key: fact.value for fact in column.facts}
        details = [fact for fact in column.facts if fact.key not in COUNT_FACTS]
        rows.append([column.name, column.kind] + [facts.get(key, "") for key in COUNT_FACTS] + [render_facts(details)])
    return lines + markdown_table(["Column", "Kind", "Filled", "Empty", "Unique", "Details"], rows, None,
                                  COLUMN_WIDTHS)


def histogram(values: list[float], low: float, high: float) -> list[int]:
    """Count numbers in equal bins.

    Args:
        values: The numbers.
        low: Lower edge of the first bin.
        high: Upper edge of the last bin.

    Returns:
        The count of each of the :data:`BINS` bins.
    """
    counts = [0] * BINS
    width = (high - low) / BINS if high > low else 1.0
    for value in values:
        counts[min(int((value - low) / width), BINS - 1)] += 1
    return counts


def svg_bars(heights: list[int], labels: list[str], title: str, colour: str, ticks: list[str],
             highest: int) -> str:
    """Draw a bar chart as inline SVG.

    Args:
        heights: The bar heights.
        labels: The label of each bar, shown on hover.
        title: The title above the plot.
        colour: The bar colour.
        ticks: The labels under the x axis: one per bar, or the two edges.
        highest: The top of the y axis, shared by the plots of one column.

    Returns:
        The SVG element.
    """
    left, top, bottom = 34, 22, 52
    plot_width, plot_height = PLOT_WIDTH - left - 8, PLOT_HEIGHT - top - bottom
    highest = max(highest, 1)
    bar_width = plot_width / max(len(heights), 1)
    base = top + plot_height
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{PLOT_WIDTH}" height="{PLOT_HEIGHT}" '
             f'viewBox="0 0 {PLOT_WIDTH} {PLOT_HEIGHT}" role="img" aria-label="{html.escape(title)}">',
             f'<text x="{PLOT_WIDTH / 2:.0f}" y="14" font-size="12" text-anchor="middle">{html.escape(title)}</text>',
             f'<text x="{left - 4}" y="{top + 8}" font-size="9" text-anchor="end">{highest}</text>',
             f'<text x="{left - 4}" y="{base}" font-size="9" text-anchor="end">0</text>',
             f'<line x1="{left}" y1="{base}" x2="{left + plot_width}" y2="{base}" stroke="#666"/>']
    for index, (value, label) in enumerate(zip(heights, labels, strict=True)):
        bar_height = plot_height * value / highest
        parts.append(f'<rect x="{left + index * bar_width + 1:.1f}" y="{base - bar_height:.1f}" '
                     f'width="{max(bar_width - 2, 1):.1f}" height="{bar_height:.1f}" fill="{colour}">'
                     f'<title>{html.escape(label)}: {value}</title></rect>')
    if len(ticks) == len(heights):
        for index, tick in enumerate(ticks):
            x = left + (index + 0.5) * bar_width
            parts.append(f'<text x="{x:.1f}" y="{base + 10}" font-size="8" text-anchor="end" '
                         f'transform="rotate(-40 {x:.1f} {base + 10})">{html.escape(tick[:14])}</text>')
    elif len(ticks) == 2:
        parts.append(f'<text x="{left}" y="{base + 12}" font-size="9">{html.escape(ticks[0])}</text>')
        parts.append(f'<text x="{left + plot_width}" y="{base + 12}" font-size="9" text-anchor="end">'
                     f'{html.escape(ticks[1])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def short_number(value: float) -> str:
    """Write an axis number in a short form.

    Args:
        value: The number.

    Returns:
        The number with at most 4 significant digits.
    """
    return f"{value:.4g}"


def numeric_svgs(name: str, cells: list[list[object]], titles: list[str], colours: list[str]) -> list[str]:
    """Draw a histogram of one numeric column of each table on shared bins.

    Args:
        name: The column name.
        cells: The cells of the column in each table.
        titles: The title of each plot.
        colours: The colour of each plot.

    Returns:
        One SVG per table, or none when no cell is a number.
    """
    values = [[found for found in (number(cell) for cell in column) if found is not None] for column in cells]
    every = [value for column in values for value in column]
    if not every:
        return []
    low, high = min(every), max(every)
    edges = [low + (high - low) * index / BINS for index in range(BINS + 1)]
    labels = [f"{short_number(edges[index])} to {short_number(edges[index + 1])}" for index in range(BINS)]
    counts = [histogram(column, low, high) for column in values]
    highest = max(max(column) for column in counts)
    return [svg_bars(column, labels, f"{name}: {title}", colour, [short_number(low), short_number(high)], highest)
            for column, title, colour in zip(counts, titles, colours, strict=True)]


def category_svgs(name: str, cells: list[list[object]], titles: list[str], colours: list[str]) -> list[str]:
    """Draw the level counts of one category column of each table on shared levels.

    Args:
        name: The column name.
        cells: The cells of the column in each table.
        titles: The title of each plot.
        colours: The colour of each plot.

    Returns:
        One SVG per table, or none when every cell is empty.
    """
    counts = [Counter(str(cell) for cell in column if not is_empty(cell)) for column in cells]
    levels = list(dict.fromkeys(level for counter in counts for level, _ in counter.most_common(MAX_BARS)))[:MAX_BARS]
    if not levels:
        return []
    highest = max(counter[level] for counter in counts for level in levels)
    return [svg_bars([counter[level] for level in levels], levels, f"{name}: {title}", colour, levels, highest)
            for counter, title, colour in zip(counts, titles, colours, strict=True)]


def plot_lines(pair: list[Section]) -> list[str]:
    """Write the plots of the numeric and category columns of a table and its pair.

    Args:
        pair: The section, and its synthetic section when there is one.

    Returns:
        The Markdown lines: two plots per row at most.
    """
    first = pair[0]
    titles = ["real", "synthetic"][:len(pair)] if len(pair) == 2 else ["data"]
    colours = [REAL_COLOUR, SYNTHETIC_COLOUR][:len(pair)]
    lines = []
    for index, column in enumerate(first.report.columns):
        cells = [section.table.column(index) if section.table is not None and index < len(section.table.header)
                 else [] for section in pair]
        if column.kind in HISTOGRAM_KINDS:
            svgs = numeric_svgs(column.name, cells, titles, colours)
        elif column.kind in BAR_KINDS:
            svgs = category_svgs(column.name, cells, titles, colours)
        else:
            continue
        if svgs:
            lines += [f"::: {{layout-ncol={len(svgs)}}}"] + [f"```{{=html}}\n{svg}\n```\n" for svg in svgs] + [":::", ""]
    return lines or ["No numeric or category column to plot.", ""]


def heading(section: Section) -> str:
    """Write the name of a table: its file name and sheet, else its glimpse label.

    Args:
        section: The section.

    Returns:
        The name.
    """
    if section.path is None:
        return section.report.label
    sheet = section.report.label.partition(" | ")[2]
    return section.path.name + (f" | {sheet}" if sheet else "")


def section_lines(pair: list[Section], rows: int) -> list[str]:
    """Write the section of one table, with its synthetic pair when there is one.

    Args:
        pair: The real section, and its synthetic section when there is one.
        rows: Rows of the head and of the tail.

    Returns:
        The Markdown lines.
    """
    names = ["Real", "Synthetic"] if len(pair) == 2 else [""]
    lines = [f"## {heading(pair[0])}", "", f"Format: `{pair[0].report.format}`. Glimpse label: `{pair[0].report.label}`."]
    if len(pair) == 2:
        lines.append(f"Synthetic table: `{heading(pair[1])}`.")
    lines.append("")
    if pair[0].table is None:
        for name, section in zip(names, pair, strict=True):
            lines += ([f"{name} glimpse:", ""] if name else []) + ["```", render(Report(tables=[section.report])).rstrip(), "```", ""]
        return lines
    lines += ["::: {.panel-tabset}", ""]
    for name, section in zip(names, pair, strict=True):
        lines += [f"### {name + ' columns' if name else 'Columns'}", ""] + column_lines(section.report) + [""]
    if rows > 0:
        for name, section in zip(names, pair, strict=True):
            if section.table is not None:
                lines += [f"### {name + ' rows' if name else 'Rows'}", ""] + row_lines(section.table, rows) + [""]
    lines += ["### Plots", ""] + plot_lines(pair) + [":::", ""]
    return lines


def difference_lines(differences: list[Difference]) -> list[str]:
    """Write the differences of ``sdg compare``.

    Args:
        differences: The differences.

    Returns:
        The Markdown lines.
    """
    if not differences:
        return ["`sdg compare` finds no difference in structure.", ""]
    errors = sum(item.level == "error" for item in differences)
    return [f"`sdg compare` finds {errors} error(s) and {len(differences) - errors} note(s).", ""] + \
        markdown_table(["Level", "Where", "Difference"],
                       [[item.level, item.where, item.message] for item in differences]) + [""]


def document(real: list[Section], synthetic: list[Section] | None, differences: list[Difference] | None,
             targets: list[Path], rows: int, secure: bool) -> str:
    """Write the Quarto document.

    Args:
        real: The sections of the targets.
        synthetic: The sections of the synthetic data, or None.
        differences: The differences of ``sdg compare``, or None.
        targets: The targets, for the list of sources.
        rows: Rows of the head and of the tail.
        secure: Print the banner of a report with real values.

    Returns:
        The text of the ``.qmd`` file.
    """
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["---", 'title: "sdg data report"', f'subtitle: "sdg {SDG_VERSION}"', f'date: "{now}"',
             "format:", "  html:", "    embed-resources: true", "    page-layout: full", "    toc: true",
             "    toc-depth: 2", "---", ""]
    if secure:
        lines += [f"<!-- {privacy.SECURE_MARKER} -->", "", "::: {.callout-warning}",
                  "Secure machine only: this report holds real values. Do not copy it to another machine, "
                  "and do not paste it into an LLM.", ":::", ""]
    lines += ["## Sources", ""] + [f"- `{target}`" for target in targets] + [""]
    if differences is not None:
        lines += ["## Differences", ""] + difference_lines(differences)
    pairs = [[section] for section in real]
    if synthetic is not None:
        pairs = [[section, synthetic[index]] if index < len(synthetic) else [section]
                 for index, section in enumerate(real)]
        pairs += [[section] for section in synthetic[len(real):]]
    for pair in pairs:
        lines += section_lines(pair, rows)
    return "\n".join(lines) + "\n"


def write_quarto(targets: list[Path], options: privacy.Options, output: Path,
                 synthetic: list[Path] | None = None, rows: int = 5) -> QuartoResult:
    """Write the Quarto report of the targets, with their synthetic pair when given.

    Args:
        targets: Files or folders: real data, or an sdg output folder.
        options: The reading options.
        output: The folder of the report. It is made when it does not exist.
        synthetic: Files or folders of the synthetic data, such as an sdg
            output folder, or None.
        rows: Rows of the head and of the tail. 0 prints no rows.

    Returns:
        The written ``.qmd`` file, and whether it holds real values.
    """
    real_report, real = table_sections(targets, options)
    synthetic_sections, differences = None, None
    if synthetic:
        synthetic_report, synthetic_sections = table_sections(synthetic, options)
        differences = compare(real_report, synthetic_report)
    secure = not all(is_sdg_output(target) for target in targets)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"quarto_report_{timestamp()}.qmd"
    sources = targets + (synthetic or [])
    path.write_text(document(real, synthetic_sections, differences, sources, rows, secure), encoding="utf-8")
    return QuartoResult(path, secure)


def render_quarto(path: Path) -> Path | None:
    """Render a ``.qmd`` file to HTML when Quarto is on the PATH.

    Args:
        path: The ``.qmd`` file.

    Returns:
        The HTML file, or None when Quarto is missing or the render fails.
    """
    quarto = shutil.which("quarto")
    if quarto is None:
        return None
    completed = subprocess.run([quarto, "render", path.name, "--to", "html"], cwd=path.parent,
                               capture_output=True, text=True, check=False)
    rendered = path.with_suffix(".html")
    return rendered if completed.returncode == 0 and rendered.is_file() else None
