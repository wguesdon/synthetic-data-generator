"""Tests of the NanoString RCC format."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from sdg.audit import find_leaks, sensitive_values
from sdg.compare import compare
from sdg.config import ConfigError, parse_config
from sdg.formats.nanostring import code_rows, read_rcc
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import render
from sdg.templates import init_config

REAL_RCC = """<Header>\r
FileVersion,1.7\r
SoftwareVersion,4.0.0.3\r
</Header>\r
\r
<Sample_Attributes>\r
ID,{sample}\r
Owner,Dr Example\r
Comments,patient {sample} relapse\r
Date,20250604\r
GeneRLF,NS_CUSTOM_PANEL\r
SystemAPF,n6_vDV1\r
</Sample_Attributes>\r
\r
<Lane_Attributes>\r
ID,{lane}\r
FovCount,280\r
ScannerID,1234C0567\r
BindingDensity,0.{density}\r
CartridgeID,CTG20250604\r
</Lane_Attributes>\r
\r
<Code_Summary>\r
CodeClass,Name,Accession,Count\r
Endogenous,IL6,NM_000600.1,{il6}\r
Housekeeping,GAPDH,NM_002046.3,3000\r
Positive,POS_A(128),ERCC_00117.1,30000\r
Negative,NEG_A(0),ERCC_00096.1,8\r
</Code_Summary>\r
\r
<Messages>\r
</Messages>\r
"""


def generate_data(data: dict, tmp_path: Path, name: str) -> Path:
    """Write a config and generate it.

    Args:
        data: The config as plain data.
        tmp_path: The pytest temporary folder.
        name: Output folder name.

    Returns:
        The output folder.
    """
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    generate(path, tmp_path / name)
    return tmp_path / name


def write_real(folder: Path, count: int) -> list[Path]:
    """Write invented RCC files with sensitive attributes.

    Args:
        folder: The output folder.
        count: Number of files.

    Returns:
        The files.
    """
    folder.mkdir()
    paths = []
    for index in range(count):
        sample = f"PT{index:03d}"
        path = folder / f"20250604_{sample}.RCC"
        text = REAL_RCC.format(sample=sample, lane=index % 12 + 1, density=40 + index, il6=100 + 7 * index)
        path.write_bytes(text.encode("utf-8"))
        paths.append(path)
    return paths


def test_round_trip(tmp_path: Path) -> None:
    """Generate, glimpse, profile and generate again: no structure error."""
    first = generate_data(yaml.safe_load(init_config(["nanostring"])), tmp_path, "first") / "dataset_1"
    report = glimpse([first], Options())
    result = profile([first], Options())
    dataset = result.config["datasets"][0]
    assert [code["name"] for code in dataset["codes"]][:2] == ["IL6", "TNF"]
    assert dataset["line_ending"] == "crlf" and dataset["samples"] == 12
    second = generate_data(yaml.safe_load(to_yaml(result)), tmp_path, "second") / "rcc"
    assert [item for item in compare(report, glimpse([second], Options())) if item.level == "error"] == []


def test_counts_follow_the_means(tmp_path: Path) -> None:
    """The negative control stays near its mean; the line ending is CRLF."""
    data = yaml.safe_load(init_config(["nanostring"]))
    data["datasets"][0]["samples"] = 60
    folder = generate_data(data, tmp_path, "out") / "dataset_1"
    files = sorted(folder.glob("*.RCC"))
    assert b"\r\n" in files[0].read_bytes()
    negatives = [float(next(row["Count"] for row in code_rows(read_rcc(path)) if row["CodeClass"] == "Negative"))
                 for path in files]
    assert np.mean(negatives) == pytest.approx(10.0, rel=0.25)


def test_model_effect(tmp_path: Path) -> None:
    """A fold change of 3 on IL6 triples its mean count in the moved group."""
    data = yaml.safe_load(init_config(["nanostring"], "model"))
    data["design"]["subjects"]["groups"] = {"group_a": 50, "group_b": 50}
    data["design"]["timepoints"] = ["baseline"]
    data["effects"][0]["fold_change"] = 3.0
    folder = generate_data(data, tmp_path, "out") / "dataset_1"
    il6 = {path.stem: float(next(row["Count"] for row in code_rows(read_rcc(path)) if row["Name"] == "IL6"))
           for path in folder.glob("*.RCC")}
    moved = np.mean([value for name, value in il6.items() if int(name[5:8]) <= 50])
    reference = np.mean([value for name, value in il6.items() if int(name[5:8]) > 50])
    assert moved / reference == pytest.approx(3.0, rel=0.25)


def test_attributes_stay_private(tmp_path: Path) -> None:
    """Sample and lane attributes never reach a report or a config, and the audit knows them."""
    paths = write_real(tmp_path / "real", 12)
    text = render(glimpse(paths, Options()))
    config = to_yaml(profile(paths, Options()))
    for secret in ("PT003", "Dr Example", "relapse", "1234C0567", "CTG20250604", "20250604"):
        assert secret not in text and secret not in config
    values = sensitive_values(paths, Options())
    assert find_leaks(text, values) == [] and find_leaks(config, values) == []
    assert "NS_CUSTOM_PANEL" in config


def test_few_files_give_defaults(tmp_path: Path) -> None:
    """Fewer than 10 files record default means (rule 5)."""
    result = profile(write_real(tmp_path / "few", 4), Options())
    assert "datasets[0].codes[*].mean" in result.defaults
    assert result.config["datasets"][0]["codes"][0]["mean"] == 100.0


def write_lab_files(folder: Path, count: int) -> list[Path]:
    """Write RCC files like those on GEO: an empty barcode, and a name in a few CartridgeID fields.

    Args:
        folder: The output folder.
        count: Number of files.

    Returns:
        The files.
    """
    paths = write_real(folder, count)
    for index, path in enumerate(paths):
        cartridge = "418273645091" if index < count - 3 else "quinn 482fov 150325"
        text = path.read_bytes().decode("utf-8").replace(
            "CartridgeID,CTG20250604\r\n", f"CartridgeID,{cartridge}\r\nCartridgeBarcode,\r\n")
        path.write_bytes(text.encode("utf-8"))
    return paths


def test_numeric_attribute_with_a_text_token_prints_no_token(tmp_path: Path) -> None:
    """Rule 8: a name in a numeric CartridgeID never prints, as it did for a NanoTube file."""
    paths = write_lab_files(tmp_path / "real", 12)
    text = render(glimpse(paths, Options()))
    assert "quinn" not in text
    assert find_leaks(text, sensitive_values(paths, Options())) == []


def test_filled_and_empty_attributes_keep_their_state(tmp_path: Path) -> None:
    """A filled Owner gets a generic text and an empty barcode stays empty, so compare finds no error."""
    paths = write_lab_files(tmp_path / "real", 12)
    result = profile(paths, Options())
    dataset = result.config["datasets"][0]
    assert dataset["text_attributes"] == ["Owner", "Comments"]
    assert {"name": "CartridgeBarcode", "empty": True} in dataset["lane_attributes"]
    second = generate_data(yaml.safe_load(to_yaml(result)), tmp_path, "second") / "rcc"
    assert [item for item in compare(glimpse(paths, Options()), glimpse([second], Options()))
            if item.level == "error"] == []
    sections = read_rcc(sorted(second.glob("*.RCC"))[0])
    assert sections["Sample_Attributes"]["Owner"] == "Owner_1"
    assert sections["Lane_Attributes"]["CartridgeBarcode"] == ""


@pytest.mark.parametrize(("change", "message"), [
    ({"text_attributes": ["ID"]}, "text_attributes"),
    ({"text_attributes": ["Operator"]}, "text_attributes"),
    ({"lane_attributes": [{"name": "CartridgeBarcode", "empty": True, "quantiles": [1.0, 2.0]}]}, "no quantiles"),
])
def test_attribute_config_errors(change: dict, message: str) -> None:
    """A text attribute must be an empty-by-default sample attribute; an empty lane attribute has no quantiles."""
    data = yaml.safe_load(init_config(["nanostring"]))
    data["datasets"][0].update(change)
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    assert message in str(caught.value)
