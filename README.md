# sdg: Synthetic Data Generator

`sdg` makes synthetic datasets from a YAML config. The config holds the structure of the real data and rounded summaries, never a real value. With the synthetic data, an agent can help with the code of a customer project while the real data stays on a secure machine.

`docs/how_it_works.md` is the full reference.

## Install

`sdg` needs Python 3.13 or later and `uv`. Add it to a project from a release tag.

```bash
uv add "sdg @ git+https://github.com/wguesdon/synthetic-data-generator@v0.14.0"
```

Two extras add optional features.

| Extra | Feature |
|---|---|
| `faker` | Real-looking person values, such as names, emails and addresses, from Faker |
| `r` | The `rds` format for an R data frame, through `pyreadr` |

```bash
uv add "sdg[faker,r] @ git+https://github.com/wguesdon/synthetic-data-generator@v0.14.0"
```

To use the `sdg` command outside a project, install it as a uv tool.

```bash
uv tool install "sdg[faker,r] @ git+https://github.com/wguesdon/synthetic-data-generator@v0.14.0"
```

To work on `sdg` itself, clone the repository and run `uv sync` from its root.

```bash
git clone https://github.com/wguesdon/synthetic-data-generator.git
cd synthetic-data-generator
uv sync
```

If a machine cannot install `sdg`, such as a cloud notebook on the secure side, upload the single script `single_script/sdg_secure.py`. The file holds the whole package, and uv installs the dependencies from the header of the file. Each command gives the same report as the installed package.

```bash
uv run sdg_secure.py glimpse data.xlsx
uv run sdg_secure.py describe design data.csv
```

The section "The single script" of `docs/how_it_works.md` gives the conditions.

An R session uses the package `sdgr` in `r/sdgr/`. It calls `sdg`, and it returns the synthetic tables as data frames. `r/sdgr/README.md` gives the install.

## Data

The repository holds no real data. The examples and the test fixtures hold invented values only. The public data test and the comparison download public benchmark datasets, such as GEO and GDC series, MIMIC-IV demo, UCI, palmerpenguins, Titanic and Synthea files. `docs/public_data.md` lists each source, and git ignores the downloads.

## Commands

| Command | Machine | Action |
|---|---|---|
| `uv run sdg glimpse <files>` | Secure machine | Prints a structure report with no real value |
| `uv run sdg profile <files> -o config.yaml` | Secure machine | Writes a config from the real data |
| `uv run sdg describe design <file>` | Secure machine | Prints the subjects per group, the timepoints, the missing samples, the replicates and the ICC |
| `uv run sdg describe groups <file>` | Secure machine | Prints the median of each group and the ratio between groups |
| `uv run sdg report <files> -o reports/` | Secure machine | Writes the glimpse and the descriptions as one report, after an audit |
| `uv run sdg relabel key.csv <files> -o <folder>` | Secure machine | Copies the real files with the generic names of a label key |
| `uv run sdg audit config.yaml <files>` | Secure machine | Fails when a config or a report holds a real value |
| `uv run sdg init <formats>` | Any | Writes a template config with a comment on each field |
| `uv run sdg schema` | Any | Prints the JSON schema of the config |
| `uv run sdg check config.yaml` | Any | Validates a config |
| `uv run sdg generate config.yaml -o <folder>` | Workstation | Writes the synthetic data, the truth tables and a manifest |
| `uv run sdg compare real.txt synthetic.txt` | Workstation | Lists the structure differences between two glimpse reports |
| `uv run sdg validate <folder>` | Workstation | Checks the files, the structure and the planted effects |

Each command has `--help`. Most commands have `--json` for an agent.

On the secure machine, `--generic-labels` replaces the real names with generic names, such as `numeric_1` for the column `CD8`. `--label-key key.csv` writes the key back to the real names. Keep the key on the secure machine. `sdg relabel` uses the key to give the real data the generic names, so the code written on the synthetic data runs on it.

In a notebook on the secure machine, `from sdg import describe` gives `describe.glimpse`, `describe.design` and `describe.groups`, which take a pandas DataFrame.

## From the real data to a coding session

| Step | Machine | Action |
|---|---|---|
| 1 | Secure machine | `uv run sdg_secure.py report data/ --generic-labels --label-key key.csv -o reports/` |
| 2 | Secure machine | Read the report, then copy it to the workstation |
| 3 | Workstation | Paste the report into a session with the `/sdg` skill. The agent writes the config and the synthetic data |
| 4 | Customer repository | `uv run sdg generate config.yaml -o data/`, then write the code with Claude Code or Codex |
| 5 | Secure machine | `uv run sdg_secure.py relabel key.csv data/ -o generic_data/`, then run the code on `generic_data/` |

The coding agent of step 4 reads the config and the synthetic data, which hold the same names, levels, counts and rounded statistics as the report. So the report is the privacy boundary. The section "From the real data to a coding session" of `docs/how_it_works.md` gives the details.

## Formats

| Format | Writes |
|---|---|
| `csv`, `tsv`, `excel` | A table file, with the sheets, title and comment rows, NA tokens, stray spaces and empty rows of a delivery |
| `parquet` | A Parquet table with typed columns and null cells |
| `rds` | An RDS file of one R data frame, with the extra `sdg[r]` |
| `olink` | An Olink NPX file in the long layout, as CSV, Excel or Parquet, or in the wide layout of NPX Manager, as Excel or CSV |
| `counts` | A bulk RNA-seq count matrix |
| `h5ad` | A single-cell AnnData file |
| `microarray` | A processed log2 intensity matrix |
| `nanostring` | A folder of nCounter RCC files |
| `fcs` | A folder of FCS files with cell populations |
| `fastq` | A folder of FASTQ files, single-end or paired-end |

The `resample` mode draws each column on its own. The `model` mode draws from a study design of subjects, groups and timepoints. It plants known effects, adds replicates and censoring, and writes truth tables, so a pipeline can be checked against a known answer.

## Example

These commands make the data of one example and check it.

```bash
uv run sdg generate example/configs/assay_long.yaml -o sdg_output/assay_long/data
uv run sdg validate sdg_output/assay_long/data
```

`example/` holds a description and the config made from it for each format.

## The agent skill

The `sdg` skill turns a description of a dataset, or pasted glimpse reports, into a config and synthetic data. In Claude Code, open the repository and run `/sdg`. Codex, opencode and pi load the same skill, as `skills/README.md` explains.

The skill never reads real data. On the secure machine, run `uv run sdg glimpse <files> -o sdg_output/reports`, read the report, and paste it into the session.

## Tests

Run the unit tests and the end-to-end test from the repository root.

```bash
uv run pytest
tests/smoke_test.sh
```

The end-to-end test runs the whole chain on each example. It writes under `sdg_output/`, which git ignores.

`scripts/test_r_package.sh` runs `R CMD check` on the R package `sdgr` in a `rocker/r-ver` container with Podman.

`tests/public_data_test.sh` runs the same chain on public files of real deliveries, such as GEO, GDC, cBioPortal, MIMIC and UCI files. It downloads them into `input/public/`. `docs/public_data.md` lists the sources and the expected result of each file.

`scripts/comparison/run_all.sh` compares `sdg` with the R package FakeDataR and the Python package Faker on 5 of these public tables. `docs/comparison.md` gives the result and the gaps of `sdg`.

## License

`sdg` and the R package `sdgr` have the MIT license. `LICENSE` gives the text.
