"""Tests of rule 10 and of the copula: rank correlations between numeric columns."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy import stats

from sdg.audit import find_leaks, sensitive_values
from sdg.config import ConfigError, parse_config
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import fact_map, render
from sdg.tables import read_tables


def write_lab(path: Path, rows: int = 200, age: bool = False) -> None:
    """Write an invented lab table with two correlated markers and one free marker.

    Args:
        path: The CSV file.
        rows: Number of rows.
        age: Add an age column that follows the first marker, which makes the table private.
    """
    rng = np.random.default_rng(5)
    first = rng.normal(0.0, 1.0, rows)
    second = 0.8 * first + 0.6 * rng.normal(0.0, 1.0, rows)
    free = rng.normal(0.0, 1.0, rows)
    header = "sample_id,il6,crp,ferritin" + (",age" if age else "")
    lines = [f"S{index:04d},{np.exp(first[index]):.4f},{np.exp(second[index]):.4f},{free[index]:.4f}"
             + (f",{int(50 + 10 * first[index])}" if age else "") for index in range(rows)]
    path.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def test_profile_records_the_correlation_and_the_copula_keeps_it(tmp_path: Path) -> None:
    """Rule 10: a strong pair records its rounded rank correlation, and the synthetic data keeps it."""
    path = tmp_path / "lab.csv"
    write_lab(path)
    report = glimpse([path], Options())
    assert fact_map(report.tables[0].facts)["correlations"].startswith("'il6' ~ 'crp' 0.")
    result = profile([path], Options())
    correlations = result.config["datasets"][0]["sheets"][0]["correlations"]
    assert len(correlations) == 1 and correlations[0]["columns"] == ["il6", "crp"]
    rho = correlations[0]["rho"]
    assert rho == round(rho, 2) and 0.7 < rho < 0.85
    text = to_yaml(result)
    assert find_leaks(text, sensitive_values([path], Options())) == []
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    generate(config, tmp_path / "out")
    table = read_tables(tmp_path / "out" / "file_1.csv")[0]
    synthetic = stats.spearmanr([float(cell) for cell in table.column(1)],
                                [float(cell) for cell in table.column(2)]).statistic
    assert abs(synthetic - rho) < 0.1


def test_code_column_keeps_its_correlation_and_its_shares(tmp_path: Path) -> None:
    """A column of exact codes, such as a passenger class 1 to 3, keeps its rank correlation and its code shares."""
    rng = np.random.default_rng(11)
    latent = rng.normal(0.0, 1.0, 300)
    codes = np.where(latent > 0.7, 1, np.where(latent > -0.2, 2, 3))
    fares = np.exp(latent + 0.5 * rng.normal(0.0, 1.0, 300)) * 10
    path = tmp_path / "fares.csv"
    path.write_text("ticket_class,fare\n" + "\n".join(f"{code},{fare:.2f}" for code, fare in zip(codes, fares,
                                                                                                 strict=True))
                    + "\n", encoding="utf-8")
    result = profile([path], Options())
    sheet = result.config["datasets"][0]["sheets"][0]
    assert "exact" in sheet["columns"][0]
    rho = sheet["correlations"][0]["rho"]
    config = tmp_path / "config.yaml"
    config.write_text(to_yaml(result), encoding="utf-8")
    generate(config, tmp_path / "out")
    table = read_tables(tmp_path / "out" / "file_1.csv")[0]
    synthetic_codes = [float(cell) for cell in table.column(0)]
    synthetic = stats.spearmanr(synthetic_codes, [float(cell) for cell in table.column(1)]).statistic
    assert rho < -0.5 and abs(synthetic - rho) < 0.1
    shares = {code: synthetic_codes.count(code) / 300 for code in (1.0, 2.0, 3.0)}
    real_shares = {code: float(np.mean(codes == code)) for code in (1, 2, 3)}
    assert all(abs(shares[float(code)] - real_shares[code]) < 0.02 for code in (1, 2, 3))


def test_private_column_and_small_table_record_no_correlation(tmp_path: Path) -> None:
    """A private column records no correlation, and a table of fewer than 50 rows records none."""
    path = tmp_path / "lab.csv"
    write_lab(path, age=True)
    facts = fact_map(glimpse([path], Options()).tables[0].facts)
    assert "'age'" not in facts["correlations"] and facts["private columns"] == "'age'"
    small = tmp_path / "small.csv"
    write_lab(small, rows=40)
    assert "correlations" not in fact_map(glimpse([small], Options()).tables[0].facts)
    assert "correlations" not in profile([small], Options()).config["datasets"][0]["sheets"][0]
    assert "age" not in render(glimpse([path], Options(generic_labels=True)))


@pytest.mark.parametrize(("columns", "message"), [
    (["il6", "missing"], "'missing' is not a numeric column"),
    (["il6", "sample_id"], "'sample_id' is not a numeric column"),
    (["il6", "il6"], "give two different columns"),
])
def test_bad_correlation_is_a_config_error(columns: list[str], message: str) -> None:
    """A correlation that names a missing, a non-numeric or the same column fails the check."""
    config = {"version": 1, "seed": 1, "datasets": [{"name": "t", "format": "csv", "file": "t.csv", "sheets": [{
        "rows": 10, "columns": [{"name": "sample_id", "kind": "identifier", "pattern": "S9999"},
                                {"name": "il6", "kind": "numeric", "quantiles": [0, 1]},
                                {"name": "crp", "kind": "numeric", "quantiles": [0, 1]}],
        "correlations": [{"columns": columns, "rho": 0.5}]}]}]}
    with pytest.raises(ConfigError, match=message):
        parse_config(config)
