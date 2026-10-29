# Changelog

This file records each change that a user can see. The format follows Keep a Changelog, and the version numbers follow Semantic Versioning.

## [Unreleased]

## [0.15.0] - 2026-10-29

### Added

- `sdg quarto TARGET... [--synthetic OUTPUT] -o DIR` writes a Quarto report of each table: the glimpse facts as a column table, the head and the tail, and a plot of each numeric and category column. With `--synthetic`, the real and the synthetic table sit side by side, and the report lists the differences of `sdg compare`. The command renders one HTML file when Quarto is on the `PATH`. The report holds real values, and `sdg audit` refuses it.
- The R package has `sdg_quarto()`.

## [0.14.0] - 2026-10-29

### Changed

- The repository is public. `uv add`, `uv tool install` and the R package install use the HTTPS URL of a release tag.
- The examples `plate_export`, `assay_long` and `fcs_panel` are new. Each example holds invented values only.
- Privacy rule 2 finds more private columns by name: common clinical scores, such as ECOG, Karnofsky, NYHA, DAS28, MELD and APACHE.
- The automatic reference level of a group contrast is the first level that names a control: `control`, `healthy`, `placebo`, `untreated`, `normal`, `vehicle` or `ctrl`.

## [0.13.0] - 2026-10-28

### Added

- `sdg init --from-schema FILE` writes a config from a database schema with no rows: a SQLite file or `CREATE TABLE` statements. Types give kinds, keys give identifiers, and a foreign key gives a `link`.
- The `rds` format reads and writes an RDS file of one R data frame, with the extra `sdg[r]`, which installs `pyreadr`. `sdg relabel` copies an RDS file too.
- Privacy rule 10: a pair of numeric columns with 50 rows of two numbers records its rank correlation, rounded to 2 decimals, when the absolute correlation is 0.2 or more. A private column records none. The glimpse prints `correlations`, the profile writes the sheet field `correlations`, and a Gaussian copula keeps them in the synthetic data. A column of codes, such as a class from 1 to 3, keeps its correlation too.
- The R package `sdgr` in `r/sdgr/` calls `sdg` from R. `sdg_glimpse()`, `sdg_report()` and `sdg_profile()` take a data frame, and `sdg_data()` returns the synthetic tables as data frames. `scripts/test_r_package.sh` runs `R CMD check` on it in a container. The package has the MIT license.
- The repository and the Python package `sdg` have the MIT license.

## [0.12.0] - 2026-10-28

### Added

- The identifier pattern `uuid` gives random UUIDs. A profile writes it for a column of UUID keys, such as the Synthea `Id` columns, and the glimpse prints `shapes: uuid`.

### Changed

- A column of digit codes where at least 10% of the cells start with `0`, such as the Synthea ZIP code `01013`, is an identifier column with the shape `09999`. In version 0.11.0 it was a number column, so the synthetic cell `1013` lost its zero.
- Privacy rule 2 adds the common shapes of a text column to the safe facts of a private table, so the Titanic cabins and the Synthea driver numbers keep their form. A private column still records counts only.
- A code provider, such as `passport_number` or `ssn`, gives way to the common shapes of the column, which hold the real form.
- A name column whose values have the form `Last, First` or `Last, Title. First` gets a Faker template of that form, such as `{{last_name}}, {{prefix}} {{first_name}}`. A provider can be such a template.

## [0.11.0] - 2026-10-28

### Added

- A text column has the fields `provider` and `locale`. With the extra `sdg[faker]`, a provider such as `email`, `name`, `phone_number`, `street_address`, `city` or `company` gives real-looking values from Faker in any Faker locale. A provider `module:Class.method` adds a user Faker provider as a plugin. The glimpse prints the provider that the column name chooses, and the profile writes it.
- The option `--drop PATTERN` of `glimpse`, `describe`, `report`, `profile` and `audit` leaves out each column of a plain table whose name matches the pattern.
- The pytest fixture `sdg_data` generates a config into a new temporary folder for a test of a project that installs `sdg`.
- A column of values such as `85%` reads as numeric with the field `suffix: '%'`. The report prints its range or its safe facts, and the synthetic cells keep the form, such as `73%`.
- A date column has the field `after`, which names an earlier date column of the row. Each synthetic date then comes on or after the earlier date, as the MIMIC discharge comes after the admission. The glimpse and the profile find such a pair.
- An identifier column has the field `link`, which names a column of unique keys in another table. The synthetic values are then keys of that table, so a join of the two synthetic tables finds each key. The glimpse prints `keys of`, and the profile writes the link, when the files are read in one run.
- `sdg.describe.profile(frame)` returns the config of a pandas DataFrame, as `sdg profile` writes it for a file.

### Fixed

- A date such as `2010-01-23T17:45:28Z`, as Synthea writes it, was text. It now reads as a date with its format.

## [0.10.0] - 2026-10-28

### Changed

- Privacy rule 2 has a narrow form. In a private table, only the private columns, such as `age` or `sex`, record counts only. The other columns record safe facts: identifier shapes, decimals, the codes of a column with 10 distinct numbers or less, and the quantiles from 5% to 95% of a column with 50 numbers or more. A code needs 10 cells, and a boolean keeps its true share when the true and the false cells each number 10 or more. No column of a private table prints its minimum or its maximum. In version 0.9.0, 4 of the 5 public tables of `docs/comparison.md` were private, so each of their numbers was drawn from 0 to 100.
- The glimpse of a private table prints `private: safe facts` and lists the `private columns`. A category column of a private table prints its level counts with generic levels, as the profile writes them.
- A label key of a private table holds its levels, and `sdg relabel` gives them their generic names.

## [0.9.0] - 2026-10-28

### Added

- The `parquet` format writes a plain table as a Parquet file with typed columns: text, 64-bit integers, doubles, dates, timestamps and booleans, with null cells. `sdg glimpse`, `sdg profile`, `sdg report`, `sdg describe` and `sdg relabel` read a plain Parquet table. A Parquet file with the Olink columns stays an Olink file. The example `lab_parquet` shows the format.
- `scripts/comparison/run_all.sh` runs `sdg`, FakeDataR 0.2.2 and Faker 40.43.0 on 5 public tables and measures the privacy and the fidelity of each output. `docs/comparison.md` gives the result and the gaps of `sdg`.

### Changed

- The `pattern` of an identifier column can be a mapping of several shapes, each with its share of the distinct values. A profile records up to 10 shapes. In version 0.8.0, the penguin sample numbers 1 to 152 kept the shape `99` only, so the synthetic column added suffixes such as `03_132` and became text.
- A text column has the field `shapes`. A profile records the 8 most common shapes of the column, which the report prints, when they fill half of its cells or more. A synthetic cell of a shape gets random upper-case letters and digits, so the Titanic tickets and cabins keep their form. Privacy rule 6 changes to allow these shapes.
- A pattern of digits only, such as `999`, counts up from a drawn start within 100 to 999, so a synthetic integer has no leading zero. The shapes `9`, `99` and `999` of one column then give distinct integers. The drawn start keeps a synthetic key from copying a real key that counts up from 100, as the MIMIC subject numbers do from 10000032.

### Fixed

- With `--generic-labels` or in a private table, a missing level of a category column, such as `NA` or `?`, became a generic level such as `level_3`, so the synthetic column had no missing cell. The level now keeps its text. It takes no number, so the other levels can get other numbers than in a label key of version 0.8.0.
- A text column with the lengths 0 to 20, the default of a private table, wrote about 5% empty cells. A filled text cell now has one character or more, and the default lengths are 1 to 20.

## [0.8.0] - 2026-10-15

### Added

- `sdg report` writes the glimpse of each file and the design and group descriptions of each plain table as one report. It audits the report against the real files, and it writes nothing when the audit fails.
- `sdg relabel key.csv <files> -o <folder>` copies real CSV, TSV and Excel files with the generic sheet, column and level names of a label key, so the code written on the synthetic data runs on the real data.
- `sdg.relabel.relabel_frame` gives a DataFrame the generic names of a label key, and with `reverse=True` gives a result the real names again.
- `describe.glimpse` has `label_key`, which writes the label key of a DataFrame.

### Changed

- `sdg compare` skips the description tables of a report, so the report of `sdg report` can replace a glimpse report.
- An `-o` value that ends with `/` is a folder, which `glimpse`, `describe` and `report` make when it does not exist.
- The audit skips a real name that has the form of a generic name, such as a sheet already called `sheet_1`.

### Fixed

- With `--generic-labels` and `--sheet`, `sdg describe` called each sheet `sheet_1`. It now prints the name of the glimpse, such as `sheet_2`.
- With `--generic-labels`, the glimpse of a long Olink file renamed the role columns, such as `SampleID` and `NPX`, and its label key held them, but the profile kept them. The glimpse now keeps them too, so the glimpse, the profile, the key and the synthetic data agree.
- With `--generic-labels`, the audit found a real column name such as `value` or `time` inside the words of a report, such as `values` or `timepoint`. In a report, a real name now matches only as a quoted item.
- With `--generic-labels`, the audit of an FCS report failed on a marker label that equals its channel name, such as `FSC-A`.

## [0.7.0] - 2026-10-14

### Added

- `single_script/sdg_secure.py` holds the whole package in one file. Upload it to a machine where `sdg` is not installed, such as a cloud notebook, and run `uv run sdg_secure.py glimpse data.xlsx`. uv installs the dependencies from the header of the file. Each command gives the same report as the installed package.
- An import of `sdg_secure` gives the Python functions, such as `sdg.describe.glimpse`.
- `scripts/build_single_script.py` writes the single script from the package source, and a test fails when the script is out of date.

## [0.6.0] - 2026-10-13

### Added

- `sdg describe design` prints the study design of one table: the subjects of each group, the timepoints in study order, the share of missing samples, the replicates, and the ICC between subjects. These facts fill the `design` and `artefacts` sections of a model-mode config.
- `sdg describe groups` prints the median and the quartiles of each group for each feature and timepoint, and the ratio and the difference of the medians against a reference group. These facts fill the `effects` section.
- Both commands find the subject, group, timepoint, replicate, feature and value columns from their names, or from an option. A statistic needs 10 values, a private table prints counts only, and `--generic-labels` prints the names of the glimpse.
- `sdg.describe.glimpse`, `sdg.describe.design` and `sdg.describe.groups` take a pandas DataFrame and return the text of the report.

## [0.5.0] - 2026-10-13

### Added

- `sdg glimpse` and `sdg profile` have `--label-key FILE`. The CSV key holds the real name behind each generic file, sheet, column and level name, so the code written on the synthetic data can run on the real data. The key stays on the secure machine.
- `sdg audit` fails when the checked file is a label key.

### Changed

- With `--generic-labels`, a column name holds the kind of the column and a number per kind, such as `numeric_1` or `categorical_1`, in place of `column_1`. The glimpse and the profile give a column the same name.

### Fixed

- With `--generic-labels`, the glimpse of an AnnData file printed the real levels of its obs and var columns.
- With `--generic-labels`, the profile of a long Olink file kept the real names of the columns with no Olink role, such as `Normalization`. The audit now searches for these names and for the sheet name.

## [0.4.0] - 2026-10-13

### Added

- The table reader finds the header row below a title, below comment lines and below a second header row, such as the row of codes of the UCI credit workbook. The rows above the header are the `preamble`, and comment rows below the data are the `footer`. The glimpse prints the header row, the comment marks and the footer row count, and the config records the place of their cells only. The synthetic file writes `note` in these cells.
- A count or microarray matrix reads a numeric column named `Chr`, `Start`, `End`, `Strand`, `Length`, `EffectiveLength` or `GeneLength` as an annotation column. The profile records its rounded deciles, and the synthetic file draws numbers for it.
- An identifier column has `as_number`, which writes number cells in a workbook. The profile sets it for a workbook column of integers.
- The audit searches the reports and configs for the text of the rows above a header and below the data.
- `sdg compare` gives an error when the header row, the comment marks or the footer row count differ.
- `tests/public_data_test.sh` adds the case `credit_default_xlsx` when LibreOffice is installed.

### Changed

- The delimiter of a text table comes from its first 50 lines that are not comment lines: the delimiter that splits the most lines into the same number of cells wins.
- The private-column rule also reads the rows above the header, and the first data row when it holds text only.

### Fixed

- The UCI credit workbook read its row of codes as the header, so the private-column rule did not see `SEX` and `AGE`, and the glimpse printed their ranges.
- A cBioPortal clinical file read its comment lines as data rows, and a GEO series matrix read as a matrix with 1 sample.
- The GSE60450 count table read its `Length` column as a sample.

## [0.3.1] - 2026-10-12

### Added

- `tests/public_data_test.sh` has 8 more cases: 6 CSV files from palmerpenguins, the MIMIC-IV demo and UCI, an `.xls` workbook and an `.xlsx` workbook.

### Fixed

- The audit reported number cells, such as `152` or `3.0`, that it found in row counts and in the version line. A value of digits only now matches as a quoted item only.
- The audit reported a value such as `6-10` inside a date, and a cell such as `999` that is its own shape.
- `?` is a standard missing token, so a column of numbers and `?` cells is numeric.
- A column named `platelets` was an identifier, because its name holds `plate`.

## [0.3.0] - 2026-10-12

### Added

- `tests/public_data_test.sh` runs the whole chain on 30 public files of real deliveries. `docs/public_data.md` lists the sources and the expected result of each file.
- A NanoString config has `text_attributes`, the sample attributes that hold a value, such as `Owner`. A lane attribute can be `empty`.

### Changed

- Each share of a tabular column gives an exact number of cells, rounded by the largest remainder method. The same seed gives other tables than version 0.2.0.
- A column name that ends with `type`, `qc`, `status`, `warning`, `group` or `class`, such as `Sample_Type`, is not an identifier.

### Fixed

- The glimpse printed a text value of a numeric RCC lane attribute, such as a person name in `CartridgeID`. It now prints shapes only.
- A numeric column with mostly `#N/A` cells was free text, and a lone number among `#N/A` cells printed as a category level.
- The audit reported `#N/A` cells, free text inside a printed level, and FCS keyword values that copy a channel name.
- The glimpse listed AnnData embeddings in file order, and `sdg validate` refused layers that were not sorted.
- `sdg compare` took the share of a missing token over the tokens only, so a rare token was an error.
- An empty cell of a numeric column could take the place of a token.
- An FCS file with a data end one byte too far gave an error, and a broken FCS file gave a traceback.

## [0.2.0] - 2026-10-12

### Added

- `sdg validate` reads FCS files back. Each event goes to the population with the highest likelihood, and the frequency of each population is tested as an effect value.
- The `olink` format reads and writes the wide layout of NPX Manager and NPX Signature, as Excel or CSV. Set `layout: wide` and describe the head, top and bottom rows in the `wide` section. `sdg glimpse`, `sdg profile` and `sdg audit` find a wide file from its `Panel` and `Assay` rows. The project name of a wide file never goes into a report or a config. A Target 96 panel of a wide file needs its 92 assays and a Target 48 panel its 45, because OlinkAnalyze refuses another count.
- `example/` holds an Olink example in the wide layout.

### Fixed

- With `--generic-labels`, the sheet name of a long Olink workbook stayed in the report and the config. It is now generic.
- An Excel sheet with a wrong dimension tag, such as `A1:A1`, read as one cell. Some R writers store such a tag. `sdg` now reads each cell that the sheet holds.

## [0.1.0] - 2026-10-11

### Added

- `pyproject.toml`, `uv.lock` and `src/sdg/version.py` define the package. The version has one source, and the root `VERSION` file holds the same value.
- The config schema covers tabular datasets, the study design, effects and artefacts. A tabular column is an identifier, a number, a category, free text, a date or a boolean.
- The `sdg schema` command prints the JSON schema of the config.
- The `sdg check` command validates a config. Each error gives the path of the field, and `--json` gives output that an agent can parse.
- Each part of a config draws from its own random stream. A new column does not change the values of the other columns.
- Each output folder gets a copy of the config, a manifest with a SHA-256 hash of each file and a notice that the data is synthetic.
- `example/configs/` holds two generic example configs.
- The `sdg glimpse` command prints a structure report of CSV, TSV and Excel files with no real value. The `-o` option writes the report to a file with a timestamp in its name.
- The privacy module applies the privacy rules. A file gets a generic label, an identifier prints as a shape, and a private table prints counts only.
- A glimpse report parses back from its text form, so a pasted report can be compared later.
- The `sdg profile` command writes a config from CSV, TSV and Excel files. A field that the privacy rules forbid gets a default, and the config header lists each default. The real file names go to the standard error stream only.
- The `sdg audit` command checks a config or a glimpse report against the real data. It fails on a real identifier, a free text value, a date, a file name or a nucleotide sequence.
- The tabular column schema records non-numeric tokens, exact values, decimal places, numbers stored as text, the distinct count of an identifier and the boolean texts. A CSV dataset records its delimiter.
- The `sdg generate` command writes synthetic CSV, TSV and Excel files in the `resample` mode. Each column draws from its own stream, and the empty rows go to drawn positions. The command refuses a folder that holds files and no sdg manifest.
- An invented identifier keeps the letters of its column and counts up in its digit positions, as real identifiers do.
- A column named like an identifier, such as `Sample date`, is a date column when its values are dates with separators.
- The `sdg compare` command lists the differences between the glimpse report of the real data and that of the synthetic output. A difference in structure, such as a name, a kind, a level set or a delimiter, is an error. A difference that can come from chance is a note.
- The `sdg` skill in `skills/claude-code/SKILL.md` holds the operating guide: the command surface, the data boundary, the intake of glimpse reports, the workflow and the completion report. The root `AGENTS.md` holds the repository rules, and `CLAUDE.md` is a copy of it.
- `scripts/sync_agent_harness.sh` writes the harness copies for Claude Code, Codex, opencode and pi, and builds the Codex and opencode adapters from the skill. With `--check`, it fails when a file differs from its source or when a harness path holds a symlink.
- The `sdg init` command writes a template config for one or more formats, with a comment on each field. The template validates.
- The design records the share of variance between subjects (`icc`) and the share of missing samples. An effect can apply at named timepoints only.
- `example/` holds a first description and the config made from it.
- The `model` mode draws feature values from the study design. Each subject carries one random intercept per feature, the `icc` sets its share of the variance, and the values follow the configured quantiles. Effects, replicates and LLOQ censoring apply after that, and `truth/effects.csv`, `truth/censored.csv` and `truth/samples.csv` record them.
- A tabular sheet can draw its rows from the design with `design` columns. The `wide` layout has one row per sample, and the `long` layout has one row per sample and feature.
- All model-mode datasets of a config share one subject list and lack the same samples.
- The artefacts can name the datasets that they apply to, and `censor_token` sets the text of a censored value.
- `example/` holds an example in the long layout.
- The `counts` format writes a bulk count matrix with negative binomial counts. The config holds the quantiles of the gene means and a DESeq2-style dispersion trend, never a value of one gene. In the model mode, each design sample is one column and an effect multiplies the mean of a gene.
- The `h5ad` format writes a single-cell AnnData file with obs and var columns, layers, embeddings and cluster marker genes. `sdg glimpse` and `sdg profile` read an AnnData file in backed mode.
- The model mode has a vectorized path for a matrix with many features.
- `example/` holds a bulk RNA-seq example and a single-cell example.
- The `microarray` format writes a processed log2 intensity matrix, probes by samples. The config holds the quantiles of the probe means and standard deviations and the spread of an array shift. In the model mode, an effect adds the log2 fold change. `--set platform=GPL570` records the platform in a profile.
- The `olink` format writes an Olink file in the long layout as CSV, Excel or Parquet. Each column has a role, found from its name, or a tabular column config. `sdg glimpse` and `sdg profile` detect an Olink file from its header. In the model mode, an effect on NPX adds the log2 fold change.
- The default identifier patterns no longer hold a literal `A`, because `A` in a pattern stands for any letter.
- The `nanostring` format writes a folder of nCounter RCC files with the sections, the code summary and the line ending of the instrument. Sample and lane attributes become generic tokens; numeric lane attributes keep rounded quantiles. In the model mode, an effect multiplies the mean of an endogenous or housekeeping code.
- The `fcs` format writes a folder of FCS files. Channel names, marker labels and ranges stay; other keyword values become generic tokens. A profile finds populations with k-means on events pooled across files, on the arcsinh scale. In the model mode, an effect multiplies the frequency of a population.
- `sdg compare` reports a different marker label of a channel as an error.
- The `fastq` format writes a folder of FASTQ files, one file or pair per sample. A profile records the read length, the quality profile, the header style, the index length and the pairing, and never a sequence. Reads come from a public reference FASTA or from a random genome, with base errors from the quality scores. A gzip file has a fixed time, so reruns give the same bytes.
- The `sdg validate` command checks an output folder: each manifest file and its hash, the structure of each dataset against the config, and each planted effect with Welch's t-test on the log2 scale. On a null dataset it gives the false positive share. `--require-effects` fails when a planted effect is not found.
- `tests/smoke_test.sh` runs the whole chain on each example: generate, glimpse, profile, audit, generate again, compare and validate.
- A private table keeps standard missing-value tokens, such as `NA` or `<LOD`, and empties other tokens.
- `sdg audit` matches a file name as a whole word and skips the words of the config schema, such as `counts`.
- A generated sheet never ends with an empty row, because a reader drops it.
- `docs/how_it_works.md` holds the full config reference and one section for each format. The README lists every command.
