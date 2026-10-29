"""Tests of the count matrix and AnnData formats."""

from __future__ import annotations

from pathlib import Path

import anndata
import numpy as np
import pytest
import yaml

from sdg.audit import find_leaks, sensitive_values
from sdg.compare import compare
from sdg.config import ConfigError, parse_config
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.matrix import read_matrix
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import fact_map, render
from sdg.templates import init_config
from sdg.validate import validate


def generate_text(text: str, tmp_path: Path, name: str = "out") -> Path:
    """Write a config text and generate it.

    Args:
        text: The YAML config.
        tmp_path: The pytest temporary folder.
        name: Output folder name.

    Returns:
        The output folder.
    """
    path = tmp_path / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    generate(path, tmp_path / name)
    return tmp_path / name


def test_model_count_matrix_has_design_samples_and_the_effect(tmp_path: Path) -> None:
    """A model-mode matrix has one column per present sample, and the effect gene moves."""
    data = yaml.safe_load(init_config(["counts"], "model"))
    data["design"]["subjects"]["groups"] = {"group_a": 30, "group_b": 30}
    data["design"]["timepoints"] = ["baseline"]
    data["effects"][0]["fold_change"] = 4.0
    data["datasets"][0]["log_mean_quantiles"] = [8.0, 9.0]
    out = generate_text(yaml.safe_dump(data), tmp_path)
    matrix = read_matrix(out / "dataset_1.tsv")
    assert len(matrix.samples) == 60 and matrix.samples[0] == "SUBJ-001_baseline"
    assert np.allclose(matrix.values, np.round(matrix.values))
    moved = matrix.values[0, :30].mean() / matrix.values[0, 30:].mean()
    other = matrix.values[1, :30].mean() / matrix.values[1, 30:].mean()
    assert moved == pytest.approx(4.0, rel=0.25) and other == pytest.approx(1.0, rel=0.25)


def test_resample_count_matrix_needs_samples() -> None:
    """The resample mode needs a sample count for a count matrix."""
    data = yaml.safe_load(init_config(["counts"]))
    del data["datasets"][0]["samples"]
    with pytest.raises(ConfigError, match="needs a sample count"):
        parse_config(data)


def test_count_matrix_round_trip(tmp_path: Path) -> None:
    """Glimpse, profile, generate and glimpse again: same structure, close fit."""
    data = yaml.safe_load(init_config(["counts"]))
    data["datasets"][0].update({"samples": 24, "library_size_cv": 0.3, "feature_count": 1500})
    first = generate_text(yaml.safe_dump(data), tmp_path, "first") / "dataset_1.tsv"
    options = Options(format="counts")
    result = profile([first], options)
    fitted = result.config["datasets"][0]
    assert fitted["feature_count"] == 1500 and fitted["feature_prefix"] == "gene_"
    assert fitted["library_size_cv"] == pytest.approx(0.3, rel=0.4)
    second = generate_text(to_yaml(result), tmp_path, "second") / "file_1.tsv"
    differences = compare(glimpse([first], options), glimpse([second], options))
    assert [item for item in differences if item.level == "error"] == []


def test_sample_names_stay_private(tmp_path: Path) -> None:
    """A glimpse shows the shapes of sample names, never the names, and the audit knows them."""
    matrix = tmp_path / "real.tsv"
    names = [f"PATIENT{index:02d}" for index in range(12)]
    rows = ["gene_id\t" + "\t".join(names)] + [f"g{gene}\t" + "\t".join(str(gene + index) for index in range(12))
                                                 for gene in range(30)]
    matrix.write_text("\n".join(rows) + "\n", encoding="utf-8")
    text = render(glimpse([matrix], Options(format="counts")))
    assert "PATIENT" not in text and "'AAAAAAA99' 12" in text
    values = sensitive_values([matrix], Options(format="counts"))
    assert find_leaks(text, values) == []
    assert find_leaks("sample: PATIENT03\n", values)[0].kind == "sample name"


def test_h5ad_output(tmp_path: Path) -> None:
    """The AnnData file has the configured obs, layers, embeddings, marker and clusters."""
    out = generate_text(init_config(["h5ad"]), tmp_path)
    adata = anndata.read_h5ad(out / "dataset_1.h5ad")
    assert adata.shape == (1000, 500)
    assert list(adata.obs["leiden"].cat.categories) == ["0", "1", "2"]
    assert "counts" in adata.layers and adata.obsm["X_umap"].shape == (1000, 2)
    assert adata.uns["sdg"]["synthetic"] == "true"
    counts = adata.layers["counts"].toarray()
    assert np.allclose(counts, np.round(counts)) and not np.allclose(adata.X.toarray(), np.round(adata.X.toarray()))
    labels = adata.obs["leiden"].astype(str).to_numpy()
    in_cluster = counts[labels == "0"].mean(axis=0)
    elsewhere = counts[labels != "0"].mean(axis=0)
    ratios = np.sort((in_cluster + 0.1) / (elsewhere + 0.1))[::-1]
    assert ratios[:10].min() > 2.0


def test_h5ad_round_trip(tmp_path: Path) -> None:
    """Profile and generate keep the obs and var structure of an AnnData file."""
    first = generate_text(init_config(["h5ad"]), tmp_path, "first") / "dataset_1.h5ad"
    result = profile([first], Options())
    assert result.config["datasets"][0]["clusters"] == {"column": "leiden"}
    second = generate_text(to_yaml(result), tmp_path, "second") / "file_1.h5ad"
    assert [item for item in compare(glimpse([first], Options()), glimpse([second], Options()))
            if item.level == "error"] == []


def test_h5ad_keys_in_any_order(tmp_path: Path) -> None:
    """An HDF5 group lists its members by name, so layers and embeddings in another order still pass."""
    data = yaml.safe_load(init_config(["h5ad"]))
    data["datasets"][0]["layers"] = ["spliced", "counts"]
    data["datasets"][0]["obsm"] = {"X_umap": 2, "X_pca": 5}
    out = generate_text(yaml.safe_dump(data, sort_keys=False), tmp_path)
    assert validate(out).issues == []
    facts = fact_map(glimpse([out / "dataset_1.h5ad"], Options()).tables[0].facts)
    assert facts["layers"] == "'counts', 'spliced'" and facts["obsm"] == "'X_pca' 5, 'X_umap' 2"


def test_length_column_is_an_annotation(tmp_path: Path) -> None:
    """GSE60450 and featureCounts have a numeric Length column after the gene column; it is not a sample."""
    names = [f"MCL1-D{letter}_L002" for letter in "ABCDEFGHIJKL"]
    rows = ["EntrezGeneID\tLength\t" + "\t".join(names)]
    rows += [f"{497000 + gene}\t{800 + gene * 37}\t" + "\t".join(str((gene * 7 + index) % 40) for index in range(12))
             for gene in range(50)]
    path = tmp_path / "counts.txt"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    matrix = read_matrix(path)
    assert matrix.samples == names and matrix.annotation_columns == ["Length"]
    options = Options(format="counts")
    result = profile([path], options)
    length = result.config["datasets"][0]["annotation_columns"][0]
    assert length["name"] == "Length" and length["decimals"] == 0 and len(length["quantiles"]) == 11
    out = generate_text(to_yaml(result), tmp_path)
    assert validate(out).issues == []
    synthetic = read_matrix(out / "file_1.txt")
    lengths = [float(cell) for cell in synthetic.annotations[0]]
    assert min(lengths) >= 800 and max(lengths) <= 2613 and all(value.is_integer() for value in lengths)
    differences = compare(glimpse([path], options), glimpse([out / "file_1.txt"], options))
    assert [item for item in differences if item.level == "error"] == []
