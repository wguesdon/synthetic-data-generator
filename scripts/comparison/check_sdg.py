"""Run the capability checks of ``capabilities.txt`` on sdg.

Each check runs sdg on the invented input of ``make_capability_input.py`` and
returns ``pass`` or ``fail`` with a note. A capability that sdg has no
function for gets no check, and the table marks it ``none``. The R check W3
runs in a container after this script, in ``check_sdgr.R``, and adds its row.

Usage:
    uv run python scripts/comparison/check_sdg.py INPUT_DIR OUT_DIR

Writes ``OUT_DIR/sdg.tsv``.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as parquet
import pyreadr
import yaml
from scipy import stats

from sdg import describe
from sdg.cli import main as sdg
from sdg.compare import compare
from sdg.generate import generate
from sdg.glimpse import glimpse
from sdg.privacy import LabelKey, Options, shape
from sdg.profile import profile, to_yaml
from sdg.report import fact_map, render
from sdg.tables import read_tables
from sdg.validate import validate

ROOT = Path(__file__).resolve().parents[2]
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}")
IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{8,30}")
FIXTURE_TEST = '''\
def test_sdg_fixture(sdg_data):
    """The sdg pytest plugin generates a config into a temporary folder."""
    assert (sdg_data("{config}") / "file_1.csv").is_file()
'''
PLUGIN = '''\
from faker.providers import BaseProvider


class WardProvider(BaseProvider):
    """A user provider of ward codes."""

    def ward_code(self):
        """Return a ward code such as K12."""
        return self.bothify("?##", letters="KLMNP")
'''
EXAMPLES = ROOT / "example" / "configs"
SEED = 42
#: The example config of each domain format.
FORMAT_EXAMPLES = {"F1": "olink_npx", "F2": "rnaseq_counts", "F3": "single_cell", "F4": "microarray_series",
                   "F5": "nanostring_rcc", "F6": "fcs_panel", "F7": "rnaseq_fastq"}


@dataclass(frozen=True)
class Context:
    """Where the checks read and write.

    Attributes:
        input: The folder of the invented input files.
        work: The folder of the outputs of the checks.
    """

    input: Path
    work: Path


def text_table(path: Path) -> pd.DataFrame:
    """Read a CSV file with every cell as text.

    Args:
        path: The file.

    Returns:
        The table, with an empty cell as an empty text.
    """
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def write_config(config: dict[str, Any], path: Path) -> Path:
    """Write a config as YAML.

    Args:
        config: The config.
        path: The file.

    Returns:
        The file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def copy_files(context: Context, name: str, files: list[Path], options: Options | None = None,
               rows: int | None = None, seed: int = SEED) -> Path:
    """Profile real files, then generate their synthetic copy once.

    Args:
        context: The check folders.
        name: Name of the copy, which is its folder.
        files: The real files.
        options: The profile options.
        rows: A row count for every sheet, or None to keep the real counts.
        seed: The config seed.

    Returns:
        The output folder of the copy.
    """
    out = context.work / name / "data"
    if out.exists():
        return out
    result = profile(files, options or Options(), seed=seed)
    if rows is not None:
        for dataset in result.config["datasets"]:
            for sheet in dataset.get("sheets", []):
                sheet["rows"] = rows
    config = context.work / name / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(to_yaml(result), encoding="utf-8")
    generate(config, out)
    return out


def visits_copy(context: Context) -> pd.DataFrame:
    """Return the synthetic copy of the invented visits table.

    Args:
        context: The check folders.

    Returns:
        The synthetic table.
    """
    return text_table(copy_files(context, "visits", [context.input / "visits.csv"]) / "file_1.csv")


def real_visits(context: Context) -> pd.DataFrame:
    """Return the invented visits table.

    Args:
        context: The check folders.

    Returns:
        The real table.
    """
    return text_table(context.input / "visits.csv")


def model_config(**changes: Any) -> dict[str, Any]:
    """Build a model-mode config of one assay with two groups.

    Args:
        **changes: Top-level fields that replace the defaults, such as ``design``.

    Returns:
        The config.
    """
    config: dict[str, Any] = {
        "version": 1, "seed": 3, "mode": "model",
        "design": {"subjects": {"groups": {"control": 40, "treated": 40}}, "timepoints": ["t1"]},
        "datasets": [{"name": "assay", "format": "csv", "file": "assay.csv", "sheets": [{"columns": [
            {"name": "subject", "kind": "design", "field": "subject"},
            {"name": "group", "kind": "design", "field": "group"},
            {"name": "visit", "kind": "design", "field": "timepoint"},
            {"name": "marker", "kind": "numeric", "quantiles": [1, 2, 4, 8, 16], "decimals": 2}]}]}],
        "effects": [{"dataset": "assay", "feature": "marker", "contrast": ["treated", "control"],
                     "fold_change": 2.0}],
    }
    config.update(changes)
    return config


def generate_model(context: Context, name: str, config: dict[str, Any]) -> Path:
    """Write a model-mode config and generate it.

    Args:
        context: The check folders.
        name: Folder name.
        config: The config.

    Returns:
        The output folder.
    """
    path = write_config(config, context.work / name / "config.yaml")
    out = context.work / name / "data"
    generate(path, out)
    return out


def check_i1(context: Context) -> tuple[bool, str]:
    """Copy the visits table with no code for each column."""
    real, synthetic = real_visits(context), visits_copy(context)
    return list(synthetic.columns) == list(real.columns), f"{len(synthetic.columns)} columns copied"


def check_i2(context: Context) -> tuple[bool, str]:
    """Copy the two sheets of a workbook in one run."""
    out = copy_files(context, "workbook", [context.input / "workbook.xlsx"])
    sheets = [table.sheet for table in read_tables(out / "file_1.xlsx")]
    return sheets == ["visits", "labs"], f"sheets {sheets}"


def check_i3(context: Context) -> tuple[bool, str]:
    """Find the header under a title row and a comment line."""
    out = copy_files(context, "titled", [context.input / "titled.csv"])
    table = read_tables(out / "file_1.csv")[0]
    real = read_tables(context.input / "titled.csv")[0]
    return table.header == real.header and len(table.preamble) == 2, f"header {table.header}"


def check_i4(context: Context) -> tuple[bool, str]:
    """Read a Parquet table and copy its columns."""
    out = copy_files(context, "parquet", [context.input / "visits.parquet"])
    written = parquet.read_schema(out / "file_1.parquet").names
    real = parquet.read_schema(context.input / "visits.parquet").names
    return written == real, f"{len(written)} columns"


def check_v1(context: Context) -> tuple[bool, str]:
    """Numbers follow a skewed distribution and copy no real value."""
    real = pd.to_numeric(real_visits(context)["crp"], errors="coerce").dropna()
    synthetic = pd.to_numeric(visits_copy(context)["crp"], errors="coerce").dropna()
    ks = stats.ks_2samp(real, synthetic).statistic
    copied = synthetic.isin(set(real)).mean()
    return ks < 0.1 and copied < 0.05, f"KS {ks:.3f}, copied {copied:.3f}"


def check_v2(context: Context) -> tuple[bool, str]:
    """Category shares stay within 0.05 of the real shares, on 1,000 rows."""
    out = copy_files(context, "visits_1000", [context.input / "visits.csv"], rows=1000)
    synthetic = text_table(out / "file_1.csv")["arm"].value_counts(normalize=True)
    real = real_visits(context)["arm"].value_counts(normalize=True)
    gap = max(abs(real[level] - synthetic.get(level, 0.0)) for level in real.index)
    return gap <= 0.05, f"largest share gap {gap:.3f}"


def check_v3(context: Context) -> tuple[bool, str]:
    """The missing count is exact within one cell, for 10 seeds."""
    counts = []
    for seed in range(10):
        out = copy_files(context, f"missing_{seed}", [context.input / "visits.csv"], seed=seed)
        counts.append(int((text_table(out / "file_1.csv")["crp"] == "").sum()))
    return all(abs(count - 20) <= 1 for count in counts), f"missing counts {sorted(set(counts))}, real 20"


def check_v4(context: Context) -> tuple[bool, str]:
    """The token <LOD keeps its share in a numeric column."""
    column = visits_copy(context)["il6"]
    share = (column == "<LOD").mean()
    numbers = pd.to_numeric(column[column != "<LOD"], errors="coerce").notna().all()
    return abs(share - 0.15) <= 0.02 and bool(numbers), f"<LOD share {share:.3f}, real 0.15"


def check_v5(context: Context) -> tuple[bool, str]:
    """Invented identifiers keep the shape A9999 and copy no real identifier."""
    synthetic = visits_copy(context)["patient_id"]
    shapes = set(map(shape, synthetic))
    copied = synthetic.isin(set(real_visits(context)["patient_id"])).sum()
    return shapes == {"A9999"} and copied <= 2, f"shapes {shapes}, copied {copied}"


def check_v6(context: Context) -> tuple[bool, str]:
    """1,000 identifiers have no repeat."""
    config = {"version": 1, "seed": 1, "datasets": [{"name": "ids", "format": "csv", "file": "ids.csv", "sheets": [
        {"rows": 1000, "columns": [{"name": "id", "kind": "identifier", "pattern": "AA9999"}]}]}]}
    out = context.work / "unique" / "data"
    generate(write_config(config, context.work / "unique" / "config.yaml"), out)
    values = text_table(out / "ids.csv")["id"]
    return values.nunique() == 1000, f"{values.nunique()} distinct of 1000"


def check_v7(context: Context) -> tuple[bool, str]:
    """Dates keep their span from an invented start and copy no real date."""
    real = pd.to_datetime(real_visits(context)["visit_date"])
    synthetic = pd.to_datetime(visits_copy(context)["visit_date"])
    real_span = (real.max() - real.min()).days
    span = (synthetic.max() - synthetic.min()).days
    copied = synthetic.isin(set(real)).sum()
    note = f"span {span} days, real {real_span}, copied {copied}"
    return abs(span - real_span) <= 0.1 * real_span and copied == 0, note


def check_v8(context: Context) -> tuple[bool, str]:
    """The true share stays within 0.05 of the real share."""
    share = (visits_copy(context)["fasting"] == "TRUE").mean()
    real = (real_visits(context)["fasting"] == "TRUE").mean()
    return abs(share - real) <= 0.05, f"true share {share:.3f}, real {real:.3f}"


def check_v9(context: Context) -> tuple[bool, str]:
    """Each ward code keeps the shape A99."""
    shapes = Counter(map(shape, visits_copy(context)["ward"]))
    return set(shapes) == {"A99"}, f"shapes {dict(shapes.most_common(3))}"


def check_v16(context: Context) -> tuple[bool, str]:
    """Two runs of one config and seed give identical files."""
    config = context.work / "visits" / "config.yaml"
    visits_copy(context)
    generate(config, context.work / "visits_again" / "data")
    same = ((context.work / "visits" / "data" / "file_1.csv").read_bytes()
            == (context.work / "visits_again" / "data" / "file_1.csv").read_bytes())
    return same, "identical bytes" if same else "the files differ"


def check_v17(context: Context) -> tuple[bool, str]:
    """A copy of the 200-row table has 1,000 rows."""
    rows = len(text_table(copy_files(context, "visits_1000", [context.input / "visits.csv"], rows=1000)
                          / "file_1.csv"))
    return rows == 1000, f"{rows} rows"


def check_d1(context: Context) -> tuple[bool, str]:
    """A planted fold change of 2 is found back, and the truth table lists it."""
    out = generate_model(context, "effect", model_config())
    effects = validate(out).effects
    truth = pd.read_csv(out / "truth" / "effects.csv")
    found = bool(effects) and all(effect.found for effect in effects)
    return found and float(truth["fold_change"].iloc[0]) == 2.0, f"found {[effect.found for effect in effects]}"


def check_d2(context: Context) -> tuple[bool, str]:
    """Subjects in two groups at three timepoints, with 10% of the later samples absent."""
    design = {"subjects": {"groups": {"control": 40, "treated": 40}}, "timepoints": ["t1", "t2", "t3"],
              "missing_samples": 0.1}
    out = generate_model(context, "design", model_config(design=design))
    samples = pd.read_csv(out / "truth" / "samples.csv")
    later = samples[samples["timepoint"] != "t1"]
    absent = 1 - later["present"].mean()
    ok = samples["group"].nunique() == 2 and samples["timepoint"].nunique() == 3 and abs(absent - 0.1) <= 0.05
    note = f"groups {samples['group'].nunique()}, timepoints {samples['timepoint'].nunique()}, absent {absent:.3f}"
    return ok, note


def check_d3(context: Context) -> tuple[bool, str]:
    """Each sample has three replicates, and about 5% of the values hold the censor token."""
    config = model_config(artefacts={"lower_limit_quantile": 0.05, "censor_token": "<LLOQ",
                                     "replicates": {"n": 3, "cv_pct": 10}})
    config["datasets"][0]["sheets"][0]["columns"].insert(3, {"name": "well", "kind": "design",
                                                             "field": "replicate"})
    out = generate_model(context, "artefacts", config)
    table = text_table(out / "assay.csv")
    per_sample = table.groupby(["subject", "visit"]).size()
    share = (table["marker"] == "<LLOQ").mean()
    return bool((per_sample == 3).all()) and abs(share - 0.05) <= 0.03, f"censored share {share:.3f}"


def check_d4(context: Context) -> tuple[bool, str]:
    """With null set, each planted fold change is 1."""
    out = generate_model(context, "null", model_config(null=True))
    truth = pd.read_csv(out / "truth" / "effects.csv")
    return bool((truth["fold_change"] == 1.0).all()), f"fold changes {sorted(set(truth['fold_change']))}"


def check_i6(context: Context) -> tuple[bool, str]:
    """A SQLite table with no rows gives a synthetic table with its columns."""
    folder = context.work / "schema"
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "config.yaml"
    code = sdg(["init", "--from-schema", str(context.input / "schema.sqlite"), "--rows", "50", "-o", str(config)])
    generate(config, folder / "data")
    table = text_table(folder / "data" / "visits.csv")
    expected = ["patient_id", "age", "crp", "visit_date", "fasting"]
    return code == 0 and list(table.columns) == expected and len(table) == 50, "sdg init --from-schema"


def check_d5(context: Context) -> tuple[bool, str]:
    """A real Spearman correlation of 0.8 stays above 0.5."""
    synthetic = visits_copy(context)
    rho = stats.spearmanr(synthetic["x"].astype(float), synthetic["y"].astype(float)).statistic
    real = stats.spearmanr(real_visits(context)["x"].astype(float), real_visits(context)["y"].astype(float)).statistic
    return rho > 0.5, f"rho {rho:.2f}, real {real:.2f}"


def check_d6(context: Context) -> tuple[bool, str]:
    """Each discharge date comes on or after its admission date."""
    synthetic = visits_copy(context)
    ordered = (pd.to_datetime(synthetic["discharge_date"]) >= pd.to_datetime(synthetic["admit_date"])).mean()
    return ordered == 1.0, f"ordered share {ordered:.2f}"


def check_d7(context: Context) -> tuple[bool, str]:
    """Each child key of the copied admissions is a key of the copied patients."""
    out = copy_files(context, "link", [context.input / "patients.csv", context.input / "admissions.csv"])
    patients = set(text_table(out / "file_1.csv")["patient_id"])
    found = text_table(out / "file_2.csv")["patient_id"].isin(patients).mean()
    return found == 1.0, f"child keys found {found:.2f}"


def check_p1(context: Context) -> tuple[bool, str]:
    """The tool flags the age and sex columns."""
    facts = fact_map(glimpse([context.input / "demographics.csv"], Options()).tables[0].facts)
    flagged = facts.get("private columns", "")
    return "'age'" in flagged and "'sex'" in flagged, f"private columns {flagged}"


def shareable_text(context: Context, name: str, files: list[Path], options: Options) -> tuple[str, Path]:
    """Write the report and the config that leave the secure machine, and the synthetic data.

    Args:
        context: The check folders.
        name: Folder name.
        files: The real files.
        options: The report and profile options.

    Returns:
        The text of the report, the config and the synthetic files, and the output folder.
    """
    folder = context.work / name
    folder.mkdir(parents=True, exist_ok=True)
    report = render(glimpse(files, options))
    result = profile(files, options, seed=SEED)
    config = folder / "config.yaml"
    config.write_text(to_yaml(result), encoding="utf-8")
    out = folder / "data"
    if not out.exists():
        generate(config, out)
    data = "\n".join(path.read_text(encoding="utf-8") for path in sorted(out.glob("file_*.csv")))
    return "\n".join([report, config.read_text(encoding="utf-8"), data]), out


def check_p2(context: Context) -> tuple[bool, str]:
    """No real email of the column contact reaches a shareable file."""
    text, _ = shareable_text(context, "contact", [context.input / "visits.csv"], Options())
    leaked = [email for email in real_visits(context)["contact"] if email in text]
    return not leaked, f"{len(leaked)} real emails in the shareable files"


def check_p3(context: Context) -> tuple[bool, str]:
    """The synthetic table holds no real column name and no real level."""
    out = copy_files(context, "generic", [context.input / "visits.csv"], Options(generic_labels=True))
    table = text_table(out / "file_1.csv")
    names = set(real_visits(context).columns) & set(table.columns)
    levels = {"placebo", "drug_a", "drug_b"} & set(table.get("categorical_1", []))
    return not names and not levels, f"real names {sorted(names)}, real levels {sorted(levels)}"


def word_in(item: str, text: str) -> bool:
    """Say whether an item occurs in a text as a whole word.

    Args:
        item: A name or a level.
        text: The searched text.

    Returns:
        True when no letter, digit or underscore touches the item.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(item)}(?![A-Za-z0-9_])", text) is not None


def check_p4(context: Context) -> tuple[bool, str]:
    """No file of the generic bundle holds a real column name or level."""
    text, _ = shareable_text(context, "generic_bundle", [context.input / "visits.csv"], Options(generic_labels=True))
    real = [name for name in real_visits(context).columns if len(name) >= 3] + ["placebo", "drug_a", "drug_b"]
    found = [item for item in real if word_in(item, text)]
    return not found, f"real names found {found}"


def check_p5(context: Context) -> tuple[bool, str]:
    """One command gives the real data the generic names of the key."""
    folder = context.work / "relabel"
    folder.mkdir(parents=True, exist_ok=True)
    options = Options(generic_labels=True, key=LabelKey())
    render(glimpse([context.input / "visits.csv"], options))
    key = folder / "key.csv"
    options.key.write(key)
    code = sdg(["relabel", str(key), str(context.input / "visits.csv"), "-o", str(folder / "copies")])
    copy = text_table(folder / "copies" / "file_1.csv")
    return code == 0 and "categorical_1" in copy.columns and set(copy["categorical_1"]) <= {
        "level_1", "level_2", "level_3"}, f"header {list(copy.columns)[:3]}"


def check_p6(context: Context) -> tuple[bool, str]:
    """A planted real identifier makes the audit fail."""
    shareable_text(context, "audit", [context.input / "visits.csv"], Options())
    text = (context.work / "audit" / "config.yaml").read_text(encoding="utf-8")
    leaked = context.work / "audit" / "leaked.yaml"
    leaked.write_text(text.replace("A9999", real_visits(context)["patient_id"].iloc[3], 1), encoding="utf-8")
    code = sdg(["audit", str(leaked), str(context.input / "visits.csv")])
    return code == 1, f"audit exit code {code}"


def check_p7(context: Context) -> tuple[bool, str]:
    """The real minimum and maximum of age are in no shareable file."""
    files = [context.input / "demographics.csv"]
    report = glimpse(files, Options())
    age = next(column for column in report.tables[0].columns if column.name == "age")
    config = profile(files, Options(), seed=SEED).config
    quantiles = next(column for column in config["datasets"][0]["sheets"][0]["columns"]
                     if column["name"] == "age")["quantiles"]
    facts = fact_map(age.facts)
    return "range" not in facts and quantiles == [0.0, 100.0], f"age facts {sorted(facts)}, quantiles {quantiles}"


def check_o1(context: Context) -> tuple[bool, str]:
    """The output is a CSV file."""
    visits_copy(context)
    path = context.work / "visits" / "data" / "file_1.csv"
    return path.is_file() and len(text_table(path)) == 200, path.name


def check_o2(context: Context) -> tuple[bool, str]:
    """The output is an xlsx workbook."""
    out = copy_files(context, "workbook", [context.input / "workbook.xlsx"])
    return (out / "file_1.xlsx").is_file(), "file_1.xlsx"


def check_o3(context: Context) -> tuple[bool, str]:
    """The output is a Parquet file."""
    out = copy_files(context, "parquet", [context.input / "visits.parquet"])
    return parquet.read_table(out / "file_1.parquet").num_rows == 200, "file_1.parquet"


def check_o4(context: Context) -> tuple[bool, str]:
    """The output is an RDS file that R reads."""
    real = context.work / "rds_input" / "visits.rds"
    real.parent.mkdir(parents=True, exist_ok=True)
    pyreadr.write_rds(str(real), text_table(context.input / "visits.csv"))
    out = copy_files(context, "rds", [real])
    frame = next(iter(pyreadr.read_r(str(out / "file_1.rds")).values()))
    return len(frame) == 200, "file_1.rds; pyreadr reads it here, and readRDS reads it in the W3 check"


def check_o5(context: Context) -> tuple[bool, str]:
    """The output holds the data, a JSON schema and a text for the LLM."""
    visits_copy(context)
    out = context.work / "visits" / "data"
    schema = context.work / "schema.json"
    code = sdg(["schema", "-o", str(schema)])
    json.loads(schema.read_text(encoding="utf-8"))
    ok = code == 0 and (out / "SYNTHETIC_DATA.md").is_file() and (out / "config.yaml").is_file()
    return ok, "config.yaml, SYNTHETIC_DATA.md and the JSON schema of sdg schema"


def check_o6(context: Context) -> tuple[bool, str]:
    """A column whose kind changed shows as a difference."""
    visits_copy(context)
    broken = context.work / "kind_changed"
    broken.mkdir(parents=True, exist_ok=True)
    table = visits_copy(context)
    table["crp"] = "value"
    table.to_csv(broken / "file_1.csv", index=False)
    differences = compare(glimpse([context.input / "visits.csv"], Options()),
                          glimpse([broken / "file_1.csv"], Options()))
    errors = [item.message for item in differences if item.level == "error" and "kind" in item.message]
    return bool(errors), errors[0] if errors else "no kind error"


def check_o7(context: Context) -> tuple[bool, str]:
    """A manifest lists a SHA-256 hash for each output file."""
    visits_copy(context)
    manifest = json.loads((context.work / "visits" / "data" / "manifest.json").read_text(encoding="utf-8"))
    hashes = [len(entry["sha256"]) == 64 for entry in manifest["files"]]
    return bool(hashes) and all(hashes), f"{len(hashes)} files with a hash"


def check_format(context: Context, example: str) -> tuple[bool, str]:
    """Generate the example of a domain format and validate it.

    Args:
        context: The check folders.
        example: Name of the example config.

    Returns:
        The result and a note.
    """
    out = context.work / f"format_{example}"
    if not out.exists():
        generate(EXAMPLES / f"{example}.yaml", out)
    issues = validate(out).issues
    return not issues, f"{example}: {len(issues)} issues"


def check_w1(context: Context) -> tuple[bool, str]:
    """A shell command makes data."""
    out = context.work / "cli" / "data"
    completed = subprocess.run(["uv", "run", "sdg", "generate", str(context.work / "visits" / "config.yaml"),
                                "-o", str(out)], capture_output=True, text=True, check=False, cwd=ROOT)
    return completed.returncode == 0 and (out / "file_1.csv").is_file(), "uv run sdg generate"


def check_w2(context: Context) -> tuple[bool, str]:
    """A Python call makes data."""
    files = generate(context.work / "visits" / "config.yaml", context.work / "python" / "data")
    return any(path.name == "file_1.csv" for path in files), "sdg.generate.generate"


def check_w6(context: Context) -> tuple[bool, str]:
    """One file runs the tool on a machine where it is not installed."""
    folder = context.work / "single_script"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "single_script" / "sdg_secure.py", folder / "sdg_secure.py")
    shutil.copy(context.input / "visits.csv", folder / "visits.csv")
    completed = subprocess.run(["uv", "run", "--no-project", "sdg_secure.py", "glimpse", "visits.csv"],
                               capture_output=True, text=True, check=False, cwd=folder)
    return completed.returncode == 0 and "# sdg glimpse" in completed.stdout, "uv run sdg_secure.py glimpse"


def check_w7(context: Context) -> tuple[bool, str]:
    """A skill file tells an agent how to write the config."""
    text = (ROOT / "skills" / "claude-code" / "SKILL.md").read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    return front.get("name") == "sdg" and "config" in text, "skills/claude-code/SKILL.md"


def check_w8(context: Context) -> tuple[bool, str]:
    """A spec file on disk gives identical data on a second run."""
    return check_v16(context)


def check_w9(context: Context) -> tuple[bool, str]:
    """A text column of percent values becomes numeric."""
    config = profile([context.input / "visits.csv"], Options(), seed=SEED).config
    column = next(item for item in config["datasets"][0]["sheets"][0]["columns"] if item["name"] == "score_pct")
    return column["kind"] == "numeric", f"score_pct kind {column['kind']}"


def provider_values(context: Context, name: str, provider: str, locale: str | None = None,
                    rows: int = 1000) -> list[str]:
    """Generate one text column with a Faker provider and return its cells.

    Args:
        context: The check folders.
        name: Folder name.
        provider: The provider.
        locale: The locale, or None.
        rows: Number of rows.

    Returns:
        The cells.
    """
    column: dict[str, Any] = {"name": "value", "kind": "text", "lengths": [1, 40], "provider": provider}
    if locale:
        column["locale"] = locale
    config = {"version": 1, "seed": SEED, "datasets": [{"name": "t", "format": "csv", "file": "t.csv",
                                                        "sheets": [{"rows": rows, "columns": [column]}]}]}
    out = context.work / name / "data"
    generate(write_config(config, context.work / name / "config.yaml"), out)
    return text_table(out / "t.csv")["value"].tolist()


def check_i5(context: Context) -> tuple[bool, str]:
    """One call copies a DataFrame that is already in memory."""
    frame = pd.read_csv(context.input / "visits.csv")
    text = describe.profile(frame, name="visits.csv", seed=SEED)
    config = write_config(yaml.safe_load(text), context.work / "frame" / "config.yaml")
    generate(config, context.work / "frame" / "data")
    columns = list(text_table(context.work / "frame" / "data" / "file_1.csv").columns)
    return columns == list(frame.columns), "sdg.describe.profile(frame)"


def check_v10(context: Context) -> tuple[bool, str]:
    """1,000 names hold at least 500 distinct full names."""
    names = provider_values(context, "names", "name")
    distinct = len({name for name in names if " " in name})
    return distinct >= 500, f"{distinct} distinct full names"


def check_v11(context: Context) -> tuple[bool, str]:
    """1,000 values are valid email addresses."""
    valid = sum(bool(EMAIL.fullmatch(value)) for value in provider_values(context, "emails", "email"))
    return valid == 1000, f"{valid} valid of 1000"


def check_v12(context: Context) -> tuple[bool, str]:
    """1,000 phone numbers hold 10 digits or more each."""
    values = provider_values(context, "phones", "phone_number")
    valid = sum(len(re.sub(r"\D", "", value)) >= 10 for value in values)
    return valid == 1000, f"{valid} of 1000 with 10 digits or more"


def check_v13(context: Context) -> tuple[bool, str]:
    """1,000 addresses hold a number and three words or more."""
    values = provider_values(context, "addresses", "address")
    valid = sum(bool(re.search(r"\d", value)) and len(value.split()) >= 3 for value in values)
    return valid == 1000, f"{valid} of 1000"


def check_v14(context: Context) -> tuple[bool, str]:
    """French and German first names come from the local name lists."""
    from faker.providers.person.de_DE import Provider as GermanNames
    from faker.providers.person.fr_FR import Provider as FrenchNames

    french = provider_values(context, "french", "first_name", "fr_FR", rows=200)
    german = provider_values(context, "german", "first_name", "de_DE", rows=200)
    ok = all(name in FrenchNames.first_names for name in french) and all(
        name in GermanNames.first_names for name in german)
    return ok, "locale fr_FR and de_DE"


def check_v15(context: Context) -> tuple[bool, str]:
    """Company, job, IBAN and IP address each give 100 valid values."""
    companies = all(provider_values(context, "companies", "company", rows=100))
    jobs = all(provider_values(context, "jobs", "job", rows=100))
    ibans = all(IBAN.fullmatch(value) for value in provider_values(context, "ibans", "iban", rows=100))
    addresses = all(ipaddress.ip_address(value) for value in provider_values(context, "ips", "ipv4", rows=100))
    return companies and jobs and ibans and addresses, "providers company, job, iban, ipv4"


def check_p8(context: Context) -> tuple[bool, str]:
    """The copy leaves out the columns that the user names."""
    options = Options(drop=re.compile("^contact$"))
    out = copy_files(context, "dropped", [context.input / "visits.csv"], options)
    columns = list(text_table(out / "file_1.csv").columns)
    return "contact" not in columns and len(columns) == 12, "--drop '^contact$'"


def check_p9(context: Context) -> tuple[bool, str]:
    """The email column of the copy holds valid invented emails."""
    result = profile([context.input / "visits.csv"], Options(), seed=SEED)
    column = next(item for item in result.config["datasets"][0]["sheets"][0]["columns"] if item["name"] == "contact")
    column.update({"provider": "email"})
    column.pop("shapes", None)
    out = context.work / "replaced" / "data"
    generate(write_config(result.config, context.work / "replaced" / "config.yaml"), out)
    values = text_table(out / "file_1.csv")["contact"]
    copied = values.isin(set(real_visits(context)["contact"])).sum()
    return all(EMAIL.fullmatch(value) for value in values) and copied == 0, f"valid emails, copied {copied}"


def check_w4(context: Context) -> tuple[bool, str]:
    """A pytest test receives generated data from the sdg_data fixture."""
    visits_copy(context)
    folder = context.work / "fixture"
    folder.mkdir(parents=True, exist_ok=True)
    config = context.work / "visits" / "config.yaml"
    (folder / "test_fixture.py").write_text(FIXTURE_TEST.format(config=config), encoding="utf-8")
    completed = subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", str(folder)],
                               capture_output=True, text=True, check=False, cwd=ROOT)
    return completed.returncode == 0, "the sdg_data fixture of the sdg pytest plugin"


def check_w5(context: Context) -> tuple[bool, str]:
    """A user-defined Faker provider gives values through sdg."""
    folder = context.work / "plugin"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ward_plugin.py").write_text(PLUGIN, encoding="utf-8")
    sys.path.insert(0, str(folder))
    values = provider_values(context, "plugin_values", "ward_plugin:WardProvider.ward_code", rows=100)
    return all(re.fullmatch(r"[KLMNP]\d\d", value) for value in values), "provider ward_plugin:WardProvider.ward_code"


CHECKS: dict[str, Callable[[Context], tuple[bool, str]]] = {
    "I1": check_i1, "I2": check_i2, "I3": check_i3, "I4": check_i4, "I5": check_i5, "I6": check_i6,
    "V10": check_v10, "V11": check_v11, "V12": check_v12, "V13": check_v13, "V14": check_v14, "V15": check_v15,
    "P8": check_p8, "P9": check_p9, "W4": check_w4, "W5": check_w5,
    "V1": check_v1, "V2": check_v2, "V3": check_v3, "V4": check_v4, "V5": check_v5, "V6": check_v6,
    "V7": check_v7, "V8": check_v8, "V9": check_v9, "V16": check_v16, "V17": check_v17,
    "D1": check_d1, "D2": check_d2, "D3": check_d3, "D4": check_d4, "D5": check_d5, "D6": check_d6,
    "D7": check_d7,
    "P1": check_p1, "P2": check_p2, "P3": check_p3, "P4": check_p4, "P5": check_p5, "P6": check_p6,
    "P7": check_p7,
    "O1": check_o1, "O2": check_o2, "O3": check_o3, "O4": check_o4, "O5": check_o5, "O6": check_o6, "O7": check_o7,
    "W1": check_w1, "W2": check_w2, "W6": check_w6, "W7": check_w7, "W8": check_w8, "W9": check_w9,
}


def main() -> int:
    """Run every sdg check and write the results.

    Returns:
        The exit code.
    """
    context = Context(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve() / "sdg_work")
    context.work.mkdir(parents=True, exist_ok=True)
    rows = []
    checks = dict(CHECKS)
    for key, example in FORMAT_EXAMPLES.items():
        checks[key] = partial(check_format, example=example)
    for key, check in checks.items():
        try:
            passed, note = check(context)
        except Exception as error:  # noqa: BLE001 - a crash is a failed check, with its message
            passed, note = False, f"error: {error}"
        rows.append((key, "pass" if passed else "fail", note))
        print(f"sdg {key}: {'pass' if passed else 'fail'} ({note})", flush=True)
    with (context.work.parent / "sdg.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["id", "result", "note"])
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
