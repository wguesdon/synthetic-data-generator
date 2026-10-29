"""Tests of ``sdg quarto``: a Quarto report of a folder, with real values, for the secure machine."""

from __future__ import annotations

import shutil
from pathlib import Path

import openpyxl
import pytest

from sdg.audit import find_leaks
from sdg.cli import main
from sdg.generate import generate
from sdg.privacy import SECURE_MARKER, Options
from sdg.profile import profile, to_yaml
from sdg.quarto import write_quarto

ROOT = Path(__file__).resolve().parents[1]


def write_lab(folder: Path) -> Path:
    """Write an invented lab table into a folder.

    Args:
        folder: The folder, which is made.

    Returns:
        The CSV file.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "lab_results.csv"
    lines = ["record_id,site,crp,fasting"]
    lines += [f"LR-{index:04d},{'north' if index % 3 else 'south'},{1.2 + index * 0.37:.2f},"
              f"{'TRUE' if index % 4 else 'FALSE'}" for index in range(40)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def synthetic_copy(real: Path, tmp_path: Path) -> Path:
    """Profile a real file and generate its synthetic copy.

    Args:
        real: The real file.
        tmp_path: The pytest temporary folder.

    Returns:
        The output folder of ``sdg generate``.
    """
    config = tmp_path / "config.yaml"
    config.write_text(to_yaml(profile([real], Options())), encoding="utf-8")
    generate(config, tmp_path / "synthetic")
    return tmp_path / "synthetic"


def test_real_and_synthetic_tables_sit_side_by_side(tmp_path: Path) -> None:
    """The report pairs the tables, prints both heads and tails, plots both and lists the differences."""
    real = write_lab(tmp_path / "real")
    synthetic = synthetic_copy(real, tmp_path)
    result = write_quarto([real.parent], Options(), tmp_path / "report", [synthetic], rows=3)
    text = result.path.read_text(encoding="utf-8")
    assert result.secure and SECURE_MARKER in text and "Secure machine only" in text
    assert "## lab_results.csv" in text and "Synthetic table: `file_1.csv`." in text
    for tab in ("### Real columns", "### Synthetic columns", "### Real rows", "### Synthetic rows", "### Plots"):
        assert tab in text
    assert "| LR-0000 | south | 1.20 | FALSE |" in text and "The last 3 rows." in text
    assert text.count("<svg") == 6 and "layout-ncol=2" in text
    assert "## Differences" in text and "sdg compare" in text


def test_synthetic_folder_alone_has_no_banner(tmp_path: Path) -> None:
    """An sdg output folder holds no real value, so its report has no secure marker."""
    synthetic = synthetic_copy(write_lab(tmp_path / "real"), tmp_path)
    result = write_quarto([synthetic], Options(), tmp_path / "report")
    text = result.path.read_text(encoding="utf-8")
    assert not result.secure and SECURE_MARKER not in text
    assert "## file_1.csv" in text and "### Rows" in text and "## Differences" not in text


def test_audit_refuses_a_secure_report(tmp_path: Path) -> None:
    """The secure marker makes the audit fail, whatever the real values are."""
    result = write_quarto([write_lab(tmp_path / "real").parent], Options(), tmp_path / "report", rows=0)
    findings = find_leaks(result.path.read_text(encoding="utf-8"), {})
    assert [finding.kind for finding in findings] == ["secure-only report"]
    assert "Rows" not in result.path.read_text(encoding="utf-8")


def test_each_sheet_is_a_section_and_other_formats_print_their_glimpse(tmp_path: Path) -> None:
    """A workbook gives one section per sheet; a FASTQ folder gives its glimpse only."""
    folder = tmp_path / "mixed"
    folder.mkdir()
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "visits"
    first.append(["visit", "value"])
    for index in range(12):
        first.append([f"visit_{index % 3}", index * 1.5])
    second = workbook.create_sheet("labs")
    second.append(["lab", "result"])
    for index in range(12):
        second.append([f"L{index:02d}", index + 0.5])
    workbook.save(folder / "study.xlsx")
    generate(ROOT / "example/configs/rnaseq_fastq.yaml", tmp_path / "fastq")
    fastq = sorted((tmp_path / "fastq").rglob("*.fastq.gz"))[0]
    text = write_quarto([folder, fastq], Options(), tmp_path / "report").path.read_text(encoding="utf-8")
    assert "## study.xlsx | sheet 'visits'" in text and "## study.xlsx | sheet 'labs'" in text
    fastq_section = text.split(f"## {fastq.name}")[1]
    assert "```" in fastq_section and "<fastq>" in fastq_section and "### Rows" not in fastq_section


def test_cli_writes_the_report_and_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The command writes the .qmd file and warns that it holds real values."""
    real = write_lab(tmp_path / "real")
    assert main(["quarto", str(real), "-o", str(tmp_path / "report"), "--no-render"]) == 0
    captured = capsys.readouterr()
    assert "keep it on the secure machine" in captured.err and "Wrote " in captured.out
    assert len(list((tmp_path / "report").glob("quarto_report_*.qmd"))) == 1
    assert main(["quarto", str(tmp_path / "missing"), "-o", str(tmp_path / "report")]) == 1


@pytest.mark.skipif(shutil.which("quarto") is None, reason="quarto is not installed")
def test_cli_renders_one_html_file(tmp_path: Path) -> None:
    """With Quarto on the PATH, the command renders one self-contained HTML file."""
    real = write_lab(tmp_path / "real")
    assert main(["quarto", str(real), "--synthetic", str(synthetic_copy(real, tmp_path)),
                 "-o", str(tmp_path / "report")]) == 0
    rendered = list((tmp_path / "report").glob("quarto_report_*.html"))
    assert len(rendered) == 1 and "<svg" in rendered[0].read_text(encoding="utf-8")
    assert not list((tmp_path / "report").glob("*_files"))
