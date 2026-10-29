"""Tests of the FCS format."""

from __future__ import annotations

import io
import re
from pathlib import Path

import flowio
import numpy as np
import pytest
import yaml

from sdg.audit import find_leaks, sensitive_values
from sdg.compare import compare
from sdg.formats.fcs import read_fcs
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import Options
from sdg.profile import profile, to_yaml
from sdg.report import render
from sdg.templates import init_config


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


def cd8_share(path: Path) -> float:
    """Return the share of events with a high CD8 signal.

    Args:
        path: An FCS file of the template.

    Returns:
        The share of events with arcsinh(FL2-A / 150) above 2.5.
    """
    events = read_fcs(path).as_array(preprocess=True)
    return float(np.mean(np.arcsinh(events[:, 3] / 150.0) > 2.5))


def test_template_files(tmp_path: Path) -> None:
    """Each file has the channels, the labels, a plausible event count and the marker."""
    folder = generate_data(yaml.safe_load(init_config(["fcs"])), tmp_path, "out") / "dataset_1"
    files = sorted(folder.glob("*.fcs"))
    assert len(files) == 6
    data = read_fcs(files[0])
    assert data.pnn_labels == ["FSC-A", "SSC-A", "FL1-A", "FL2-A", "Time"]
    assert data.pns_labels[2:4] == ["CD4", "CD8"]
    assert 8000 <= data.event_count <= 12000
    assert data.text["sdg_synthetic"].startswith("sdg ") and data.text["cyt"] == "value"
    time = data.as_array(preprocess=True)[:, 4]
    assert np.all(np.diff(time) >= 0)


def test_round_trip(tmp_path: Path) -> None:
    """Glimpse, profile, generate and glimpse again: no structure error."""
    first = generate_data(yaml.safe_load(init_config(["fcs"])), tmp_path, "first") / "dataset_1"
    report = glimpse([first], Options())
    result = profile([first], Options())
    assert len(result.config["datasets"][0]["populations"]) == 6
    second = generate_data(yaml.safe_load(to_yaml(result)), tmp_path, "second") / "fcs"
    assert [item for item in compare(report, glimpse([second], Options())) if item.level == "error"] == []


def test_model_effect_on_a_population(tmp_path: Path) -> None:
    """An effect of fold change 2 on cd8_t raises its share in the moved group."""
    data = yaml.safe_load(init_config(["fcs"], "model"))
    data["design"]["subjects"]["groups"] = {"group_a": 8, "group_b": 8}
    data["design"]["timepoints"] = ["baseline"]
    data["datasets"][0]["weight_cv"] = 0.05
    folder = generate_data(data, tmp_path, "out") / "dataset_1"
    moved = [cd8_share(folder / f"SUBJ-{index:03d}_baseline.fcs") for index in range(1, 9)]
    reference = [cd8_share(folder / f"SUBJ-{index:03d}_baseline.fcs") for index in range(9, 17)]
    assert np.mean(moved) > 1.35 * np.mean(reference)


def test_keywords_stay_private(tmp_path: Path) -> None:
    """Keyword values never reach a report or a config, and the audit knows them."""
    folder = tmp_path / "real"
    folder.mkdir()
    rng = np.random.default_rng(1)
    for index in range(3):
        events = np.abs(rng.normal(1000, 300, size=(500, 2))).astype(np.float32)
        metadata = {"op": "Jane Operator", "sample id": f"PT{index:03d}-S1", "date": "14-NOV-2024"}
        with (folder / f"PT{index:03d}.fcs").open("wb") as handle:
            flowio.create_fcs(handle, events.ravel(), ["FSC-A", "FL1-A"], opt_channel_names=["", "CD4"],
                              metadata_dict=metadata)
    paths = sorted(folder.glob("*.fcs"))
    text = render(glimpse(paths, Options()))
    config = to_yaml(profile(paths, Options()))
    for secret in ("Jane Operator", "PT001-S1", "14-NOV-2024"):
        assert secret not in text and secret not in config
    values = sensitive_values(paths, Options())
    assert find_leaks(text, values) == [] and find_leaks(config, values) == []
    assert "CD4" in config and "sample id" in config


def shift_data_end(content: bytes) -> bytes:
    """Move the data end offset of an FCS file one byte too far, as the MACSQuant writes it.

    Args:
        content: A valid FCS file written by flowio.

    Returns:
        The file with the offset plus one in the HEADER and in ``$ENDDATA``.
    """
    end = int(content[34:42])
    content = content[:34] + f"{end + 1:>8}".encode() + content[42:]
    return re.sub(rb"(\$ENDDATA.)" + str(end).encode(), rb"\g<1>" + str(end + 1).encode(), content, count=1)


def test_data_end_one_byte_too_far_is_read(tmp_path: Path) -> None:
    """Three public MACSQuant files have this fault; the reader drops the extra byte."""
    buffer = io.BytesIO()
    flowio.create_fcs(buffer, np.arange(40, dtype=np.float32), ["FSC-A", "SSC-A"])
    path = tmp_path / "macsquant.fcs"
    path.write_bytes(shift_data_end(buffer.getvalue()))
    with pytest.warns(UserWarning, match="incorrect data offset"):
        data = read_fcs(path)
    assert data.event_count == 20 and data.as_array(preprocess=False)[-1].tolist() == [38.0, 39.0]


def test_unreadable_file_is_a_clear_error(tmp_path: Path) -> None:
    """A broken file gives a ValueError with its name, which the command prints, not a traceback."""
    path = tmp_path / "broken.fcs"
    path.write_bytes(b"oi21j08cn\n")
    with pytest.raises(ValueError, match="broken.fcs: not a readable FCS file"):
        glimpse([path], Options())


def test_keyword_copy_of_a_channel_name_is_not_a_leak(tmp_path: Path) -> None:
    """A FACSCalibur file copies channel names and labels into BD keywords, which rule 3 prints."""
    folder = tmp_path / "real"
    folder.mkdir()
    metadata = {"bdp1n": "FSC-A", "bdpmfl2": "CD4", "op": "Jane Operator"}
    events = np.abs(np.random.default_rng(1).normal(1000, 300, size=(500, 2))).astype(np.float32)
    with (folder / "tube_1.fcs").open("wb") as handle:
        flowio.create_fcs(handle, events.ravel(), ["FSC-A", "FL1-A"], opt_channel_names=["", "CD4"],
                          metadata_dict=metadata)
    paths = sorted(folder.glob("*.fcs"))
    values = sensitive_values(paths, Options())
    assert "FSC-A" not in values and "CD4" not in values and "Jane Operator" in values
    assert find_leaks(render(glimpse(paths, Options())), values) == []
    assert "CD4" in sensitive_values(paths, Options(generic_labels=True))


def test_marker_label_that_copies_its_channel_name_is_not_a_leak(tmp_path: Path) -> None:
    """With generic labels, a $PnS that equals its $PnN, such as FSC-A, is printed as the channel name."""
    folder = tmp_path / "real"
    folder.mkdir()
    events = np.abs(np.random.default_rng(1).normal(1000, 300, size=(500, 2))).astype(np.float32)
    with (folder / "tube_1.fcs").open("wb") as handle:
        flowio.create_fcs(handle, events.ravel(), ["FSC-A", "FL1-A"], opt_channel_names=["FSC-A", "CD4"])
    paths = sorted(folder.glob("*.fcs"))
    options = Options(generic_labels=True)
    values = sensitive_values(paths, options)
    assert "FSC-A" not in values and values["CD4"] == "marker label"
    assert find_leaks(render(glimpse(paths, options)), values) == []
