# How sdg works

`sdg` makes synthetic data from a YAML config. The config holds the structure of the real data and rounded summaries. It never holds a real value. With the synthetic data, an agent can help with the code of a customer project while the real data stays on a secure machine.

This document is the full reference. `example/` holds a description and a config for each format.

## The privacy boundary

The secure machine holds the real data. The workstation holds the synthetic data and the agent session. Only a glimpse report or a config crosses from the secure machine to the workstation, after a person reads it.

| Step | Machine | Command |
|---|---|---|
| Read the structure of the real data | Secure machine | `sdg glimpse` |
| Write one audited report of the real data | Secure machine | `sdg report` |
| Write a config from the real data | Secure machine | `sdg profile` |
| Check a report or a config for real values | Secure machine | `sdg audit` |
| Write or edit a config | Workstation | `sdg init`, `sdg schema`, `sdg check` |
| Make the synthetic data | Workstation | `sdg generate` |
| Compare the real and the synthetic structure | Workstation | `sdg glimpse`, then `sdg compare` |
| Check the output and the planted effects | Workstation | `sdg validate` |
| Give the real data the generic names, to run the code | Secure machine | `sdg relabel` |

There are three methods to get a config:

1. Run `sdg profile` on the real data, then `sdg audit` on the config.
2. Run `sdg report` or `sdg glimpse` on the real data. Paste the report into the agent session, and the agent writes the config.
3. Describe the dataset to the agent, and the agent writes the config.

## From the real data to a coding session

Use this procedure when the real data is on a secure machine, such as a cloud notebook, and you write the code with an agent on another machine. The single script `sdg_secure.py` runs the commands of the secure machine; see [The single script](#the-single-script).

1. On the secure machine, run `uv run sdg_secure.py report data/ --generic-labels --label-key key.csv -o reports/`. The command writes one report and audits it. Keep `key.csv` on the secure machine.
2. Read the report. Then copy it to the workstation.
3. On the workstation, paste the report into a session with the `/sdg` skill. The agent writes `sdg_output/<project>/config.yaml`, generates the data and compares it with the report.
4. Copy `config.yaml` to the customer repository, and run `uv run sdg generate config.yaml -o data/` there. Write the code with Claude Code or Codex on the synthetic data.
5. On the secure machine, run `uv run sdg_secure.py relabel key.csv data/ -o generic_data/`. Run the code on `generic_data/`.
6. To give a result the real names again, call `relabel_frame(result, "key.csv", file, reverse=True)`.

The report is the privacy boundary. In step 4, the coding agent reads `config.yaml` and the synthetic data, which hold the same names, levels, counts and rounded statistics as the report. A zero-retention LLM in step 3 keeps the report out of the logs of the LLM provider, but it does not change what the coding agent sees in step 4. So decide in step 1 what the report can show:

- If a column name or a level is sensitive, use `--generic-labels`.
- A table that is not private shows the minimum and the maximum of each numeric column, with 3 significant digits. A profile keeps 11 quantiles from the minimum to the maximum. If one value of one person is sensitive, make the file private with `--private`.

## Commands

Run each command from the repository root with `uv run sdg`. Each command exits with 0 on success and 1 on a failure.

| Command | Main options | Action |
|---|---|---|
| `sdg glimpse <targets>` | `--format`, `--private`, `--drop`, `--generic-labels`, `--label-key`, `--recursive`, `-o` | Prints a structure report with no real value. `-o DIR` writes `DIR/glimpse_<timestamp>.txt` |
| `sdg profile <targets>` | The glimpse options, `--seed`, `--set KEY=VALUE`, `-o` | Writes a config. The real file names go to the standard error stream only |
| `sdg describe design <file>` | `--subject`, `--group`, `--timepoint`, `--replicate`, `--feature`, `--value`, `--sheet`, `--private`, `--generic-labels`, `-o` | Prints the study design of one table with no real value. `-o DIR` writes `DIR/design_<timestamp>.txt` |
| `sdg describe groups <file>` | The design options, `--reference` | Prints the median of each group and the ratio between groups. `-o DIR` writes `DIR/groups_<timestamp>.txt` |
| `sdg report <targets>` | The glimpse options, `--label-key`, `-o` | Writes the glimpse and the descriptions of the targets as one report, after an audit. `-o DIR/` writes `DIR/report_<timestamp>.txt` |
| `sdg relabel <key> <targets> -o <folder>` | `--recursive` | Writes a copy of each real CSV, TSV, Excel and Parquet file of the key, with the generic names |
| `sdg audit <checked> <targets>` | The glimpse options, `--json` | Fails when the checked config or report holds a real value |
| `sdg schema` | `-o` | Prints the JSON schema of the config |
| `sdg init <formats>` | `--mode`, `-o` | Writes a template config with a comment on each field |
| `sdg init --from-schema <file>` | `--rows`, `-o` | Writes the config of a database schema with no rows: a SQLite file or `CREATE TABLE` statements |
| `sdg check <config>` | `--json` | Validates a config. Each error gives the path of the field |
| `sdg generate <config> -o <folder>` | `--json` | Writes the synthetic data. The folder must be empty or an earlier sdg output |
| `sdg compare <real> <synthetic>` | `--json` | Lists the differences between two glimpse reports |
| `sdg validate <folder>` | `--alpha`, `--require-effects`, `--json` | Checks the files, the structure and the planted effects |

The `--drop` option takes a pattern of column names. The glimpse, the descriptions, the report and the profile leave out each column of a plain table whose name matches it, as if the table did not have it. So the synthetic data has no such column. Give the audit the same option. The audit still searches for the values of a dropped column. A table whose only private column is dropped, such as `--drop '^age$'`, is no longer private.

The `-o` option of `glimpse`, `describe` and `report` takes a folder or a path prefix. A value that ends with `/` is a folder, and the command makes it when it does not exist.

The `--format` option sets the format of every target. A count matrix and a microarray matrix need it, because they have the suffix of a plain table. The other formats are found from the suffix, and an Olink file from its header.

`sdg compare` gives an error for a difference in structure, such as a column name, a kind or a common level. It gives a note for a difference that can come from chance. A level, a token or a cell type that the synthetic data lacks is a note when its share of the filled cells is below 5%.

The `--set` option passes a format setting to `sdg profile`:

| Setting | Format | Effect |
|---|---|---|
| `platform=GPL570` | `microarray` | Records the platform |
| `reference=genome.fa` | `fastq` | Records the path of a public FASTA |
| `library=rnaseq` | `fastq` | Samples transcripts evenly |
| `populations=8` | `fcs` | Sets the number of populations that k-means finds |
| `cofactor=500` | `fcs` | Sets the cofactor of the arcsinh scale |

## The single script

The file `single_script/sdg_secure.py` holds the whole `sdg` package. Use it on a machine where `sdg` is not installed, such as a cloud notebook on the secure side. Upload the one file, then run it with uv from the folder of the file.

```bash
uv run sdg_secure.py glimpse data.xlsx
uv run sdg_secure.py describe design data.csv
uv run sdg_secure.py describe groups data.csv --group disease_status
uv run sdg_secure.py report data/ -o reports/
uv run sdg_secure.py relabel key.csv data/ -o generic_data/
uv run sdg_secure.py --help
```

Each command works with the same options, and each report is the same as the report of the installed package. The usage lines and the error lines start with `sdg`, not with the name of the file.

| Condition | Reason |
|---|---|
| uv can download packages from PyPI or from a mirror | The header of the file lists the dependencies in the PEP 723 form. uv installs them in a cached environment on the first run. For a mirror, set `UV_DEFAULT_INDEX` to its URL |
| Python 3.13 or later is on the machine, or uv can download it | The package needs Python 3.13. The script was checked on Python 3.13 and 3.14 |

To get the file, copy it from the release tag of the repository, for example with `git show v0.14.0:single_script/sdg_secure.py > sdg_secure.py`.

To use the Python functions, import the file one time. Then `import sdg` loads the package from the file. Put the analysis in a script in the folder of `sdg_secure.py`, and run it with the dependencies of the single script.

```python
import pandas as pd

import sdg_secure
from sdg import describe

frame = pd.read_excel("panel.xlsx")
print(describe.glimpse(frame, name="panel.xlsx"))
```

```bash
uv run --with-requirements sdg_secure.py analysis.py
```

`scripts/build_single_script.py` writes the file from `src/sdg`. The file holds the source of each module as string literals, one literal for each source line, and an import hook loads `sdg` from them. The hook writes nothing to disk. `tests/test_single_script.py` fails when the file differs from the source, so each change to `src/sdg/` needs a new build.

## Privacy rules

`sdg glimpse` and `sdg profile` follow these rules. Each rule has a test.

1. An identifier column records the shape of its values only. A letter becomes `A` and a digit becomes `9`. A column of digit codes where at least 10% of the cells start with `0`, such as the ZIP code `01013`, is an identifier column too, and its shape keeps the leading zeros, as `09999`. A column is an identifier when its name holds `subject`, `donor`, `patient`, `sample`, `plate`, `barcode` or `id`. The name `platelets` holds `plate`, but it is not an identifier. A name that ends with `type`, `qc`, `status`, `warning`, `group` or `class`, such as `Sample_Type`, is not an identifier, because its values are levels.
2. A table with a private column, such as age, sex, date of birth or a clinical score such as ECOG or NYHA, is private. A file whose name matches `--private`, by default `clinical`, is private too. An empty pattern matches each file name. To make no file private by its name, use `--private '^$'`. The rule also reads the rows above the header, and the first data row when it holds text only. In a private table, the private columns record counts only, and the report lists them as `private columns`. The other columns record the safe facts of the table below. No column of a private table prints its minimum or its maximum, because each is the value of one person. A private table keeps standard missing-value tokens, such as `NA`, `?` or `<LOD`, and the profile writes defaults for the other values.

   | Column kind | Safe fact |
   |---|---|
   | `identifier` | The shapes |
   | `numeric` | With 10 distinct numbers or less, the codes, each with 10 cells or more. Else, with 50 numbers or more, the quantiles from 5% to 95% and the median. The decimals in both cases |
   | `categorical` | The level counts, with generic levels. A private column prints them too |
   | `boolean` | The true and the false counts, when each is 10 or more |
   | `date` | The format |
   | `text` | The common shapes, when the 8 most common shapes fill half of the cells or more |
3. Column names, sheet names, category labels and public feature names stay, because the analysis code uses them. `--generic-labels` replaces them with generic names. A generic column name holds the kind of the column, such as `numeric_2`. The section [Generic labels and the label key](#generic-labels-and-the-label-key) gives the details.
4. A column with more than 100 distinct text values records shapes and lengths only.
5. A number is rounded to 3 significant digits. A column with less than 10 numbers records its kind and missing share only. A column is numeric when at least half of its filled cells are numbers and its other cells hold 5 distinct texts or less. Standard missing tokens, such as `NA` or `#N/A`, do not count in the half.
6. A free text column records its length range. When its 8 most common shapes fill half of its cells or more, it also records these shapes with their shares, as the report prints them. A text column whose name marks a person value, such as `Email` or `First Name`, records a Faker provider, which the name alone chooses. A text column is categorical only when its values repeat.
7. A date column records the intervals between dates, never an absolute date.
8. File names, FCS keyword values, FASTQ header fields and RCC sample and lane attributes become generic tokens. A numeric RCC lane attribute with a text value prints shapes only. The rows above a header and the comment rows below the data record the count and the place of their cells only.
9. A report or a config never holds a nucleotide sequence of 20 bases or more.
10. A pair of numeric columns with 50 rows of two numbers records its Spearman rank correlation, rounded to 2 decimals, when the absolute correlation is 0.2 or more. A table records the 20 strongest pairs, and only when it has 40 numeric columns or less. A private column records no correlation.

`sdg audit` reads the real data again. It searches the checked text for each identifier, free text value, date, file name, header field and keyword value, for the text of the rows above a header and below the data, and for any sequence. It skips a standard missing token, such as `#N/A`, and an FCS keyword value that copies a channel name or a kept marker label. It also skips free text that is part of a column name or of a category level that the report prints by rule 3. An identifier or a date inside a printed level is a finding. A value of digits only, such as `152`, matches as a quoted item only, because the report prints counts and rounded numbers. A value with digits and no letter, such as `6-10`, matches as a whole token. A value that is its own shape, such as `999`, is skipped. A date matches anywhere.

With `--generic-labels`, the audit also searches for each real column name, sheet name, level and FCS marker label. A report prints each name in quotes, so in a report a name matches only as a quoted item, such as `'CD8'`. The words of the report, such as `value column` or `timepoint`, then do not match a real column called `value` or `time`. In a config, a name matches as a whole word, and a word of the config schema, such as `group`, is skipped. A marker label that equals its channel name, such as `FSC-A`, is skipped, because the report prints the channel name.

### Generic labels and the label key

With `--generic-labels`, `sdg glimpse` and `sdg profile` replace the real names with generic names. A column gets the name of its kind and a number. The number counts the columns of that kind, in column order. For example, `CD8` and `CD4` become `numeric_1` and `numeric_2`, and `disease_status` becomes `categorical_1`. The kind names are `identifier`, `numeric`, `categorical`, `text`, `date` and `boolean`.

A category level becomes `level_<n>`, and `level_1` is the most common level. A missing level, such as `NA`, `N/A`, `-` or `?`, keeps its text, because code reads it as a missing cell. A private table keeps it too. A limit level such as `<18` becomes generic, because it can be a band. A sheet becomes `sheet_<n>`. The glimpse and the profile give a column the same generic name. So a config that an agent writes from a generic report and a config from `sdg profile` use the same names.

A role column of a long Olink file keeps its standard name, such as `SampleID` or `NPX`, in the glimpse, in the profile and in the label key, because the Olink reader finds the roles by name. A role column counts in the numbering of its kind. The levels of a role column, such as the panels, print as generic levels, and the label key does not hold them. The feature column and the annotation columns of a count matrix or a microarray matrix keep their own generic names.

`--label-key FILE` writes a CSV file with the real name behind each generic name. Each row is a `file`, a `sheet`, a `column` or a `level`. A level row gives the generic name of its column in `column`. In a workbook, `table` gives the generic sheet name. In an AnnData file, `table` is `obs` or `var`.

```text
item,file,table,column,generic,real
file,,,,file_1.csv,delivery/markers.csv
column,file_1.csv,,,numeric_1,CD8
column,file_1.csv,,,categorical_1,disease_status
level,file_1.csv,,categorical_1,level_1,patient
```

If you write a label key, keep it on the secure machine. The key holds the real names. `sdg audit` fails when the checked file is a label key. The audit skips a real name that has the form of a generic name, such as a sheet that is already called `sheet_1`, because that name tells nothing.

### Run the code on the real data

The code that was written on the synthetic data uses the generic names. To run it on the real data, give the real data the same names on the secure machine.

```bash
uv run sdg relabel key.csv data/ -o generic_data/
```

The command writes a copy of each CSV, TSV, Excel and Parquet file that the key holds. Each copy has the generic file label as its name, such as `file_1.xlsx`, as the synthetic file has. In each copy, the sheets, the columns and the levels have their generic names. The rows above the header and below the data stay as they are.

| Item | Behavior |
|---|---|
| A file that the key does not hold | The command skips it and prints its name |
| An existing copy | The command stops. Choose a new output folder |
| A CSV or TSV copy | The copy is UTF-8 and keeps the delimiter |
| A formula cell of a workbook | The copy keeps the stored value, without the formula |
| A private table | The columns and the levels get generic names, as the report prints them |

The copies hold real data. Keep them on the secure machine. Do not run the glimpse on a copy, because the private-column rule does not see a generic name such as `numeric_3` as an age.

For a DataFrame, `relabel_frame` gives the same names. With `reverse=True`, it gives the generic names of a result their real names again.

```python
import pandas as pd
from sdg.relabel import relabel_frame

real = pd.read_excel("data/panel.xlsx", sheet_name="Results")
frame = relabel_frame(real, "key.csv", "data/panel.xlsx", sheet="Results")
result = frame.groupby("categorical_1")["numeric_1"].median().reset_index()
print(relabel_frame(result, "key.csv", "data/panel.xlsx", sheet="Results", reverse=True))
```

The `file` argument takes the real path, the real file name or the generic label. The `sheet` argument takes the real or the generic sheet name. It is necessary only when the key names several sheets of the file. `describe.glimpse(frame, name=..., generic_labels=True, label_key="key.csv")` writes the key of a DataFrame.

The key does not hold the generic names of assays, panels, FCS markers, RCC codes, matrix features or annotation columns.

## Real-looking values

A text column with a `provider` field gets its values from Faker, such as names, emails, phone numbers or addresses. Faker invents each value, so no real value reaches the synthetic data. Install the extra on the machine that generates the data.

```bash
uv add "sdg[faker]"
```

```yaml
- {name: Email, kind: text, lengths: [10, 40], provider: email}
- {name: Nom, kind: text, lengths: [3, 20], provider: last_name, locale: fr_FR}
- {name: Ward, kind: text, lengths: [3, 3], provider: "my_wards:WardProvider.ward_code"}
```

| Field | Content |
|---|---|
| `provider` | The name of a Faker method, such as `name`, `first_name`, `email`, `phone_number`, `street_address`, `city`, `country`, `postcode`, `company`, `job`, `url`, `iban` or `ipv4`. A value `module:Class.method` adds a user Faker provider, which works as a plugin |
| `locale` | A Faker locale, such as `fr_FR` or `de_DE`. The default is `en_US` |

A provider can also be a Faker template, such as `"{{last_name}}, {{prefix}} {{first_name}}"`. A profile and a report choose the provider from the column name. A name column whose values have the form `Last, First` or `Last, Title. First`, as the Titanic names do, gets the template of that form. A code provider, such as `ssn`, `passport_number`, `postcode`, `phone_number`, `iban` or `ipv4`, gives way to the common shapes of the column when they fill half of its cells, because the shapes hold the real form, such as the Synthea passport `X19277260X`. The table gives the name rules, on the lower-case name with `_` between words. The first rule that matches wins. A category column keeps its levels and gets no provider. A column whose values are UUID keys gets no provider, because its name, such as `ORGANIZATION`, names the key and not a value.

| Name holds | Provider |
|---|---|
| `email` | `email` |
| `phone`, `mobile`, `telephone`, `tel`, `fax` | `phone_number` |
| `company`, `employer`, `organisation` | `company` |
| `first_name`, `given_name`, `forename`, or `first` alone | `first_name` |
| `last_name`, `surname`, `family_name`, `maiden`, or `last` alone | `last_name` |
| `ssn`, `social_security` | `ssn` |
| `passport` | `passport_number` |
| `name` alone, or `customer_name`, `patient_name` and other person names | `name` |
| `ip`, `ip_address` | `ipv4` |
| `street`, `address` | `street_address` |
| `city`, `town` | `city` |
| `country` | `country` |
| `zip`, `postcode`, `postal` | `postcode` |
| `website`, `url`, `homepage` | `url` |
| `job`, `occupation` | `job` |
| `iban` | `iban` |

`sdg check` tests the provider and the locale when Faker is installed. A config with a provider stays valid on a machine without Faker, such as the secure machine, and `sdg generate` then asks for the extra. A provider of the form `module:Class.method` imports that module, so use only a module that you trust.

## Design and group descriptions

`sdg glimpse` describes each column on its own. `sdg describe design` and `sdg describe groups` describe how the rows of one table relate. Run them on the secure machine, run `sdg audit` on each report, and paste the reports into the session with the glimpse report. Their facts fill the `design`, `effects` and `artefacts` sections of a model-mode config.

### Column roles

Each command finds the columns of six roles from their names. The roles are the `field` values of a `kind: design` column.

| Role | Option | A name that holds | Condition |
|---|---|---|---|
| subject | `--subject` | `subject`, `donor`, `patient` or `participant` | Else the first identifier column with repeated values |
| group | `--group` | `group`, `arm`, `cohort`, `disease`, `diagnosis`, `condition` or `treatment` | 2 to 10 levels. In a long table, one value for each sample |
| timepoint | `--timepoint` | `visit`, `timepoint`, `week`, `day`, `month` or `cycle` | 20 values or less, and the value changes within a subject |
| replicate | `--replicate` | `replicate`, `rep` or `well` | None |
| feature | `--feature` | `analyte`, `assay`, `marker`, `protein`, `gene` or `target` | A value column is also necessary |
| value | `--value` | `value`, `result`, `concentration`, `npx` or `intensity` | Numeric. A feature column is also necessary |

To set a role, give the option and the real column name. To use no column for a role, give `""`. With a feature column and a value column, the table is long, with one row for each sample and feature. In the other tables, each numeric column with no role is a feature.

A sample is a subject in a group at a timepoint. Without a subject column, each row is one subject. A subject whose samples got three treatments is in three groups, so each treatment is a sample of that subject. Rows of the same sample and feature are replicates.

When no group column is found, the report lists the `group candidates`. `sdg describe groups` then stops and names the candidates.

### The design report

| Fact | Content | Config field |
|---|---|---|
| Each `<group>` line | The subjects and the samples of the group, and the samples at each timepoint | `design.subjects.groups` |
| Each `<timepoint>` line | The timepoint labels in study order. A label such as `baseline` or `screening` comes first, then the number in each label sets the order | `design.timepoints` |
| `missing_samples` | The share of the subject and timepoint pairs after the first timepoint that have no row | `design.missing_samples` |
| `icc` | The median over the features of the share of variance between subjects | `design.icc` |
| `replicates.n` | The most common count of rows of one sample and feature, when the table has a replicate column | `artefacts.replicates.n` |
| `replicates.cv_pct` | The median coefficient of variation between replicates, in percent | `artefacts.replicates.cv_pct` |
| `rows per sample` | The most common count of rows of one sample and feature, when the table has no replicate column. The rows are replicates, or levels of a factor with no role | None |
| `subjects in several groups` | The subjects with samples in more than one group | None |

The ICC uses the subjects with two samples or more. Each value becomes a normal score, because the model mode draws on the normal scale, and a one-way random effects ICC follows. The ICC needs 10 such subjects, and the CV needs 10 samples with replicates.

If `subjects in several groups` is more than 0, the group is a condition within a subject. The model mode plants an effect between groups of subjects only, so it cannot plant such an effect.

### The groups report

Each sample counts once, because the report averages its replicates first. The report has one line for each feature, and one line for each feature and timepoint when the table has a timepoint column. Each group gives its sample count `n`, its median and its quartiles. A group with less than 10 samples prints `n <10` and no statistic.

Each other group gets a contrast with the reference group: the ratio of the medians, its log2, and the difference of the medians. The reference is the `--reference` level, else the first level that names a control, such as `control`, `healthy`, `placebo`, `untreated` or `vehicle`, else the largest group.

To write an effect, set `contrast` to the group and the reference. For a linear feature, set `fold_change` to the ratio. For a log2 feature, such as NPX, set `fold_change` to 2 to the power of the difference.

### Privacy of the descriptions

- Counts print as they do in the glimpse.
- A median, a quartile, a ratio, an ICC and a CV need 10 values or more, and they print with 3 significant digits.
- Names follow rule 3. With `--generic-labels`, a report prints the generic names of the glimpse, so the label key of the glimpse also applies to it.
- A private table prints counts only in a description, so no ICC, CV, median or ratio prints. To see these statistics, drop the private columns, such as age and sex, from a DataFrame first.
- A subject identifier never prints. The value of a long table feature prints as a level of the feature column, or as `feature_<n>` when the glimpse prints shapes only.

### Python functions

The functions in `sdg.describe` take a pandas DataFrame, for data that comes from a query or a merge in a notebook. Each function returns the text of its report.

```python
import pandas as pd
from sdg import describe

frame = pd.read_parquet("merged.parquet")
print(describe.glimpse(frame, name="panel.csv"))
print(describe.design(frame, subject="Patient ID", group="disease_status", timepoint="Visit"))
print(describe.groups(frame, group="disease_status", reference="healthy"))
```

| Argument | Default | Content |
|---|---|---|
| `subject`, `group`, `timepoint`, `replicate`, `feature`, `value` | None | The real column of each role. None finds the column by name, and `""` sets no column |
| `reference` | None | The real reference group of `groups` |
| `name` | `dataframe` | The name of the data. Its suffix sets the format, so `panel.xlsx` gives an Excel report that compares with the glimpse of a synthetic workbook. A name that matches `private` makes the table private |
| `generic_labels` | `False` | Replace the column names and the levels with generic names |
| `private` | `clinical` | A name that matches this pattern makes the table private |

An index that is not the default range holds data, such as sample names, so it becomes the first column.

`describe.profile(frame, name="trial.csv", seed=7)` returns the YAML config of a DataFrame, as `sdg profile` writes it for a file. The suffix of `name` sets the format of the synthetic file: `.csv`, `.tsv`, `.xlsx` or `.parquet`. Read the config before it leaves the secure machine. If the group column is in another table, merge the two tables first.

## One report for the secure machine

`sdg report` writes the glimpse of each file and the descriptions of each plain table as one file. Then it runs the audit of `sdg audit` on the text. If the audit fails, the command writes no report and prints each real value that it found.

```bash
uv run sdg report data/ --generic-labels --label-key key.csv -o reports/
```

| Part | Content |
|---|---|
| Head | `# sdg report <version>` and `# audit: passed` |
| Glimpse | The report of `sdg glimpse` for each file |
| Design | The report of `sdg describe design` for each table with a header in a CSV, TSV, Excel or Parquet file, or in a long Olink export that is not Parquet. A file of another format gets the note `not a plain table, no description` |
| Groups | The report of `sdg describe groups` for the same tables. A table with no two groups gets the note `no two groups to compare` |

A description finds the roles of the columns from their names. If a role is wrong, run `sdg describe` on that table with the role options, and paste that report as well. `sdg compare` reads the glimpse part of a report and skips the description tables, so the report can replace a glimpse report.

## Tests on synthetic data

A project that installs `sdg` gets the pytest fixture `sdg_data`. A test calls it with the path of a config. The call generates the data into a new temporary folder and returns the folder.

```python
def test_pipeline_reads_the_panel(sdg_data):
    data = sdg_data("config.yaml")
    result = run_pipeline(data / "file_1.xlsx")
    assert not result.empty
```

So the tests of the customer repository run on the synthetic data, and the same code later runs on the real data.

## Use from R and from a database

The R package `sdgr` in `r/sdgr/` calls the `sdg` command from R. `r/sdgr/README.md` gives the install. On the secure machine, `sdg_glimpse()`, `sdg_report()` and `sdg_profile()` take a data frame or the paths of files. A data frame goes to a temporary CSV file, and the function deletes the file before it returns.

```r
library(sdgr)
sdg_glimpse(visits)
sdg_report(visits, output = "reports")
sdg_profile(visits, output = "visits.yaml")
```

On the workstation, `sdg_data()` generates a config into a temporary folder and returns its tables as a named list of data frames. `sdg_generate()` and `sdg_read()` do the two steps one at a time.

```r
tables <- sdg_data("visits.yaml")
visits <- tables[["file_1.csv"]]
```

The option `sdgr.command` or the environment variable `SDGR_COMMAND` gives the command when `sdg` is not on the `PATH`, such as `uv run --project ~/synthetic-data-generator sdg`. On a secure machine with the single script only, the command is `uv run sdg_secure.py`.

`sdg init --from-schema clinic.sql -o config.yaml` writes a config from a database schema, which holds no value. The schema is a SQLite file, such as `clinic.sqlite`, or a text of `CREATE TABLE` statements. Each table becomes a CSV dataset of 100 rows, or of the `--rows` count. The declared type gives the kind of each column, and a primary key, a foreign key or an identifier name gives an identifier column. A foreign key gives a `link` to the key that it references. A text column whose name marks a person value gets a provider. Each row count, quantile, length, interval and share is a default, so edit them, or write the result of a query as a Parquet file and run the report on it.

## The config

A config has these top-level fields:

| Field | Default | Content |
|---|---|---|
| `version` | Required | Always `1` |
| `seed` | Required | 0 or more. The same config and seed give the same data |
| `mode` | `resample` | `resample` draws each column on its own. `model` draws from the design, with effects and artefacts |
| `null` | `false` | `true` sets each fold change to 1, for a check of the false positive rate |
| `design` | None | The study design. The `model` mode needs it |
| `datasets` | Required | One entry per file or folder |
| `effects` | `[]` | Planted effects. They need the `model` mode |
| `artefacts` | None | Censoring and replicates. They need the `model` mode |

Each dataset has a `name`, which effects use, a `format` and a `file`. The `file` is a path relative to the output folder. For a format that writes one file per sample, such as `fcs`, the `file` is a folder.

### Design

| Field | Default | Content |
|---|---|---|
| `subjects.prefix` | `SUBJ-` | Start of a subject identifier, which is the prefix and a number, such as `SUBJ-001` |
| `subjects.groups` | Required | Each group and its subject count |
| `timepoints` | `[]` | Timepoint labels in study order. With none, each subject has one sample |
| `icc` | `0.6` | Share of the variance of a feature that lies between subjects |
| `missing_samples` | `0.0` | Share of the samples after the first timepoint that are absent |

A sample identifier is the subject identifier and the timepoint, such as `SUBJ-001_week_4`. All model-mode datasets of a config share one subject list and lack the same samples. `truth/samples.csv` lists each sample with its group, its timepoint and its presence.

### The model

In the `model` mode, each subject draws one random intercept per feature, and each sample adds its own residual:

```text
z = sqrt(icc) * subject + sqrt(1 - icc) * residual
value = quantile function of the feature (normal CDF of z)
```

The value therefore follows the quantiles of the feature. Then the effects, the replicates and the censoring apply, in that order.

### Effects

| Field | Default | Content |
|---|---|---|
| `dataset` | Required | Dataset name |
| `feature` | Required | Feature name, such as a numeric column, an assay, a gene or a population |
| `contrast` | Required | Two groups. The first group moves; the second is the reference |
| `fold_change` | Required | Ratio of the moved group to the reference. 1.0 is no effect |
| `timepoints` | `[]` | Timepoints where the effect applies. Empty applies it at each timepoint |

On a `linear` feature, an effect multiplies the value. On a `log2` feature, such as NPX or a log2 intensity, it adds the log2 fold change. `truth/effects.csv` records each effect at each timepoint.

### Artefacts

| Field | Default | Content |
|---|---|---|
| `lower_limit_quantile` | `0.0` | Share of the values censored at a lower limit. The limit is this quantile of the feature |
| `censor_token` | None | Text of a censored value, such as `<LLOQ`. None writes the limit |
| `replicates.n` | None | Replicates of each sample, such as wells |
| `replicates.cv_pct` | None | Coefficient of variation between replicates, in percent |
| `datasets` | `[]` | Datasets that the artefacts apply to. Empty applies them to each model-mode dataset |

`truth/censored.csv` lists each censored value with its true value and its limit.

## Formats

| Format | Writes | Model mode | Found by |
|---|---|---|---|
| `csv`, `tsv`, `excel`, `parquet`, `rds` | One table file | Yes, with design columns | Suffix. A Parquet file is an Olink file when its header has the Olink columns |
| `olink` | One NPX file in the long or the wide layout | Yes | Header, or the `Panel` and `Assay` rows |
| `counts` | One count matrix | Yes | `--format counts` |
| `h5ad` | One AnnData file | No | Suffix |
| `microarray` | One log2 intensity matrix | Yes | `--format microarray` |
| `nanostring` | A folder of RCC files | Yes | Suffix |
| `fcs` | A folder of FCS files | Yes | Suffix |
| `fastq` | A folder of FASTQ files | Design samples only | Suffix |

A format with "Yes" can be the target of an effect. In the model mode, a format with a `samples` field takes its samples from the design. In the resample mode, it needs `samples`.

In a `pattern` field, `A` stands for any letter and `9` for any digit. Every other character stays as written. An invented identifier keeps the letters of its column and counts up in its digit positions, as `QX-0001` and `QX-0002`. The pattern `uuid` gives random UUIDs, such as `1d604da9-9a81-4ba9-80c2-de3375d59b40`. A profile writes it for a column whose values are all UUIDs, and the glimpse prints `shapes: uuid`. A pattern of digits only is an integer, so it has no leading zero. The pattern `999` counts up from a drawn start between 100 and 999, so a synthetic key does not copy a real key that counts up from 100.

### CSV, TSV, Excel, Parquet and RDS

A tabular dataset has `sheets` and, for a text file, an optional `delimiter`. A CSV, TSV, Parquet or RDS file has exactly one sheet.

An RDS file holds one R data frame. It needs the extra `sdg[r]`, which installs `pyreadr`. A synthetic RDS file holds numbers, texts and booleans. `pyreadr` writes a date as text in the format of the column, so convert it with `as.Date` in R.

A Parquet file holds one table with typed columns. It has no rows above the header and no footer, and a null is an empty cell. The synthetic file gives each column a type.

| Column kind | Parquet type |
|---|---|
| `identifier` | Text, or a 64-bit integer with `as_number` |
| `numeric` | A 64-bit integer when `decimals` is 0, else a double. A column with `as_text`, or with tokens such as `<LOD`, is text |
| `categorical`, `text` | Text |
| `date` | A date when `date_format` is `%Y-%m-%d`, else a timestamp. A profile writes `%Y-%m-%d %H:%M:%S` for a timestamp column, and `%Y-%m-%d 00:00:00` when each time is midnight |
| `boolean` | Boolean |

`sdg` reads the first 50 lines of a text file that are not comment lines. It chooses the delimiter that splits the most of these lines into the same number of cells. So a title line or a decimal comma does not change the choice.

The header does not have to be the first row. `sdg` finds the header row of each table with these steps:

1. It skips the comment rows and the blank rows at the top. A comment row starts with `#` or `!`, as in a cBioPortal file, a featureCounts file or a GEO series matrix. A missing token such as `#N/A` is not a comment.
2. It skips each row that fills half of the table width or less, such as a title in cell A1.
3. The next rows of text cells are the header candidates. A blank row between them does not stop them.
4. The rows after the candidates must hold a column of numbers, dates or booleans, which shows where the data starts. The header is the candidate with the most filled cells that names each such column. So the header search takes the names row of the UCI credit workbook, and not the row of codes `X1` to `X23` above it, because the codes row leaves cell A1 empty.
5. If no such column follows, as in a table of text only, the header is the first row after the comment rows. If there is no candidate, the header is the first row.

The kind of a whole column confirms the header, and not one cell. The synthetic file keeps the kind of each column, but not each cell, so it reads with the same header row. A text column of codes such as `01` does not confirm a header, because the synthetic file writes invented words in it.

The rows above the header are the `preamble`. The comment rows at the end of a table, such as `!series_matrix_table_end`, and the blank rows above them are the `footer`. The glimpse prints the header row, the comment marks and the footer row count. The config records each of these rows with the fields below, and the synthetic file writes `note` in each of its cells. So code that skips 4 rows or the comment lines of the real file reads the synthetic file the same way.

| Row field | Default | Content |
|---|---|---|
| `cells` | `1` | Filled cells. `0` writes a blank row |
| `start` | `0` | Position of the first filled cell, from 0 |
| `mark` | None | `#` or `!` at the start of the first cell |

| Sheet field | Default | Content |
|---|---|---|
| `name` | `sheet_1` | Sheet name. A text file ignores it |
| `rows` | None | Data rows, empty rows included. A sheet without design columns needs it |
| `empty_rows` | `0` | Rows with every cell empty. The last row is never empty |
| `columns` | Required | Columns in file order. A name can repeat |
| `layout` | `wide` | `wide`: one row per sample. `long`: one row per sample and feature |
| `features` | `[]` | The features of a long sheet, each with `name`, `quantiles`, `scale` and `decimals` |
| `preamble`, `footer` | `[]` | The rows above the header and the comment rows below the data, each with `cells`, `start` and `mark` |
| `correlations` | `[]` | Rank correlations of pairs of numeric columns, each with `columns` and `rho`, such as `{columns: [il6, crp], rho: 0.78}`. A Gaussian copula keeps them. A sheet with design columns has none |

Each column has a `name`, a `kind`, a `missing` share and an optional `na_token`, the text of an empty cell. Each share gives an exact number of cells, rounded by the largest remainder method, in random positions. So a token in 1 cell of 17 is in 1 cell of the synthetic column too, and an empty cell never takes the place of a token.

| Kind | Fields | Content |
|---|---|---|
| `identifier` | `pattern`, `distinct`, `as_number`, `link` | Values of the shape. With `link`, such as `{dataset: file_1, column: patient_id}`, the values are keys of an identifier column of unique keys in another table, so a join of the two synthetic tables finds each key. The glimpse prints `keys of` for such a column, and the profile writes the link. A mapping such as `{'99': 0.6, '999': 0.35, '9': 0.05}` gives several shapes with their shares of the distinct values. With `distinct`, the values repeat in blocks. `as_number` writes number cells in a workbook, and the pattern then holds digits only |
| `numeric` | `quantiles`, `tokens`, `exact`, `decimals`, `as_text`, `scale`, `suffix` | Numbers from the quantiles. `tokens` are texts such as `NA` with their share; `exact` are frequent values such as 0. `suffix` writes a text such as `%` after each number, as `73%`. A column whose cells are mostly numbers with a percent sign reads as numeric with the suffix `%` |
| `categorical` | `levels` | Each level with its share. The shares sum to 1 |
| `text` | `lengths`, `shapes`, `provider`, `locale` | Invented words of a length in the range. `shapes` gives common shapes, such as `A99`, with their shares of the filled cells. A cell of a shape gets random upper-case letters and digits. `provider` gives real-looking values from Faker instead, as the section [Real-looking values](#real-looking-values) tells |
| `date` | `interval_days`, `anchor`, `date_format`, `as_text`, `after` | The anchor date, by default 2000-01-01, plus a drawn interval. With `after`, the date of an earlier date column of the row plus a drawn interval, so a discharge comes after its admission. The glimpse prints `after` and the `days after` of each date column that comes on or after an earlier date column in each row, and the profile writes them |
| `boolean` | `true_share`, `true_text`, `false_text` | True or false in the share |
| `design` | `field` | `subject`, `group`, `timepoint`, `sample`, `replicate`, or in a long sheet `feature` and `value` |

`quantiles` are values at evenly spaced probabilities from 0 to 1. Two values give a uniform range, and eleven give the deciles. `as_text` writes a number or a date as a text cell in a workbook, as some deliveries do.

A sheet with a `design` column draws its rows from the design in the model mode. In the wide layout, each numeric column is a feature. In the long layout, the `features` list holds the features, and the design columns of field `feature` and `value` hold the feature name and its value.

```yaml
- name: assay
  format: csv
  file: assay.csv
  sheets:
    - layout: long
      features:
        - {name: IL6, quantiles: [0.8, 2.2, 4.1, 7.2, 40.0], decimals: 2}
      columns:
        - {name: Patient ID, kind: design, field: subject}
        - {name: Visit, kind: design, field: timepoint}
        - {name: Analyte, kind: design, field: feature}
        - {name: Concentration, kind: design, field: value}
```

### Olink

An Olink dataset has one of two layouts, which the `layout` field sets:

- The `long` layout has one row per sample and assay. It is the CSV export of NPX Manager and the export of Olink Explore. `sdg` reads and writes it as CSV, Excel or Parquet.
- The `wide` layout has one row per sample and one column per assay. It is the Excel or CSV export of NPX Manager and NPX Signature for the qPCR platforms, such as Target 96. `sdg` reads and writes it as Excel or CSV.

`sdg` finds a long file from its header and a wide file from its `Panel` and `Assay` rows. OlinkAnalyze 5.1.0 reads a synthetic file of each layout with `read_NPX()`.

In the long layout, each column is a role column or a tabular column. A role column has `kind: olink` and a `role`: `sample_id`, `index`, `olink_id`, `uniprot`, `assay`, `panel`, `plate_id`, `well_id`, `sample_qc`, `lod`, `missing_freq` or `value`. A profile finds the role from the column name, such as `SampleID` or `NPX`. Any other column, such as `Normalization`, keeps a tabular column config.

| Field | Default | Content |
|---|---|---|
| `layout` | `long` | `long` or `wide` |
| `columns` | Required in the long layout | Columns in file order. The wide layout has no columns |
| `assays` | Required | Each assay with `assay`, `olink_id`, `uniprot`, `panel`, `unit`, `lod`, `below_lod` and `quantiles` |
| `samples`, `sample_pattern` | None, `SMPL-999` | Sample count and shape in the resample mode |
| `plates`, `plate_pattern` | `1`, `PLT-9` | Plate count and shape. Samples fill the plates in order, by well |
| `qc_pass`, `qc_warn`, `qc_warn_share` | `PASS`, `WARN`, `0.0` | Sample QC texts and the share of warnings |
| `scale` | `log2` | `log2` for NPX, `linear` for a `Quant` column |
| `decimals` | `4` | Decimal places of a value |
| `delimiter`, `sheet` | `;`, `Sheet1` | Delimiter of a text file and sheet of a workbook |
| `wide` | See below | The parts of a wide file around the values |

A wide file has these parts, from the top:

1. Two head rows. The first cell holds the project name, and the second cell holds the software version. The cell under the project name holds the data type, such as `NPX Data`.
2. The top rows, such as `Panel`, `Assay`, `Uniprot ID` and `OlinkID`. Each top row gives one field of each assay. A `Unit` row gives the unit of a quantified value.
3. An empty row, and then one row per sample. A sample row holds the sample identifier, one value per assay, and the cells of the columns after the assays.
4. An empty row, and then the bottom rows, such as `LOD` and `Missing Data freq.`.

A profile never records the project name, so a synthetic file holds `project_1`. `example/configs/olink_wide.yaml` is a complete example.

OlinkAnalyze refuses a wide file when a Target 96 panel does not have 92 assays or a Target 48 panel does not have 45 assays. So `sdg check` refuses a wide config with another count for a panel whose name holds `Target 96` or `Target 48`. Internal control columns, such as `Inc Ctrl 1`, do not count. If a description names only some assays, add the other assays with invented names.

| `wide` field | Default | Content |
|---|---|---|
| `project` | `project_1` | Text of the first cell |
| `software` | None | Text of the second cell. OlinkAnalyze reads the version from it |
| `data_type` | `NPX Data` | Text of the second row |
| `top_rows` | `Panel`, `Assay`, `Uniprot ID`, `OlinkID` | Labels of the top rows. One label must be `Assay` |
| `columns` | None | The columns after the assays. None writes a `Plate ID` column for each panel, then a `QC Warning` column for each panel |
| `bottom_rows` | `LOD`, `Missing Data freq.` | The rows under the samples |
| `deviation_sd` | `0.1` | Spread of a QC deviation column |

A column after the assays has a `label`, a `role`, a `panel` and two optional fields:

| `role` | Writes |
|---|---|
| `plate_id` | The plate of the sample, with a plate identifier for its panel |
| `sample_qc` | `qc_pass` or `qc_warn`. The `warn_share` field gives the share of warnings in this column; without it, `qc_warn_share` applies |
| `deviation` | A QC deviation from the median. The `control` field gives the text of the `Uniprot ID` row, such as `Inc Ctrl` |

A bottom row has a `label` and a `role`:

| `role` | Writes |
|---|---|
| `lod` | The `lod` of each assay |
| `missing_freq` | The `below_lod` share of each assay |
| `text` | The `text` field for each assay, such as `Intensity` |
| `number` | A value for each assay, drawn from the `quantiles` field |

A profile records the warning share of each QC column, because a rare warning can be absent from one panel.

### Count matrix

A count matrix has genes in rows and samples in columns. Counts follow a negative binomial distribution. The config holds no value of one gene.

| Field | Default | Content |
|---|---|---|
| `features` or `feature_count` | One is required | Gene identifiers, or a count of generated names `<feature_prefix><n>` |
| `feature_column`, `annotation_columns` | `gene_id`, `[]` | Header of the identifier column, and the annotation columns. A text column, given by its header, repeats the identifier. A numeric column has `name`, `quantiles` and `decimals` |
| `log_mean_quantiles` | Required | Quantiles of log2(mean count + 1) across genes |
| `dispersion`, `dispersion_extra` | `0.05`, `1.0` | The dispersion trend: `dispersion + dispersion_extra / mean` |
| `library_size_cv` | `0.2` | Spread of the library sizes |
| `biological_cv` | `0.3` | Spread of a gene mean between samples |
| `samples`, `sample_pattern` | None, `SMPL-99` | Sample count and shape in the resample mode |
| `preamble`, `footer` | `[]` | The rows above the header, such as the command line of featureCounts, and the comment rows below the data. Each row has `cells`, `start` and `mark`, as for a CSV table |

In the model mode, each design sample is one column, named by its sample identifier. An effect on a gene with a mean near zero cannot be found.

A numeric column after the gene column is a sample, except when its name is `Chr`, `Start`, `End`, `Strand`, `Length`, `EffectiveLength` or `GeneLength`. Such a column describes the gene, as the `Length` column of featureCounts does. The profile records its rounded deciles, and the synthetic file draws a number for each gene, so code that divides the counts by the gene length works.

### AnnData

An AnnData dataset writes one `.h5ad` file of single-cell counts. It draws on its own in both modes.

| Field | Default | Content |
|---|---|---|
| `cells`, `cell_pattern` | Required, `AAAAAAAAAAAAAAAA-9` | Cell count and the shape of a cell name |
| `features` or `feature_count` | One is required | Gene identifiers, or a count of generated names |
| `log_mean_quantiles`, `dispersion`, `dispersion_extra`, `library_size_cv` | As for a count matrix | The count model of a cell |
| `obs`, `var` | `[]` | Cell and gene columns, with the tabular column kinds |
| `layers` | `[]` | Layer names. Each layer holds the raw counts |
| `obsm` | `{}` | Each embedding and its dimensions, such as `X_umap: 2` |
| `uns` | `[]` | Names of the unstructured entries. Each is an empty mapping |
| `x_kind` | `counts` | `counts`, or `log1p` of counts per 10,000 |
| `clusters` | None | `column`, a categorical obs column; `marker_genes` and `marker_log2fc` raise genes in each cluster |

The file carries `uns["sdg"]`, a marker that the data is synthetic. The glimpse lists the layers, the embeddings and the `uns` entries in name order, because an HDF5 file does not always keep the write order.

### Microarray

A microarray dataset is a processed matrix of log2 intensities, probes in rows and samples in columns. Version 1 does not read raw CEL files.

| Field | Default | Content |
|---|---|---|
| `platform` | None | Platform accession, such as `GPL570` |
| `features` or `feature_count` | One is required | Probe identifiers, or a count of generated names `probe_<n>` |
| `feature_column`, `annotation_columns` | `ID_REF`, `[]` | As for a count matrix |
| `mean_quantiles`, `sd_quantiles` | Required | Quantiles of the probe means and of the probe standard deviations |
| `array_shift_sd` | `0.1` | Spread of the shift of a whole array |
| `decimals` | `3` | Decimal places |
| `samples`, `sample_pattern` | None, `GSM9999999` | Sample count and shape in the resample mode |
| `preamble`, `footer` | `[]` | The rows above the header and below the data, such as the metadata lines and the end line of a GEO series matrix. Each row has `cells`, `start` and `mark`, as for a CSV table |

### NanoString

A NanoString dataset writes one RCC file per sample, named `<sample>.RCC`, with the sections and the line ending of the instrument.

| Field | Default | Content |
|---|---|---|
| `codes` | Required | Each code with `code_class`, `name`, `accession` and `mean`, the mean count across samples |
| `gene_rlf`, `system_apf`, `assay_type` | `NS_CODESET`, `n6_vDV1`, None | Public names of the code set and the system |
| `header`, `sample_attributes` | File version 1.7 | Header fields, and the sample attribute names in order |
| `text_attributes` | `[]` | Sample attributes, such as `Owner`, that hold the generic text `<name>_1`. `ID`, `Date`, `GeneRLF`, `SystemAPF` and `AssayType` always hold a value. The other attributes are empty |
| `lane_attributes` | `[]` | Each lane attribute with `quantiles` and `decimals`, with `empty: true` for an empty value, or with no quantiles for a generic text. `ID` holds the lane number |
| `count_cv`, `lane_cv` | `0.4`, `0.2` | Spread of an endogenous mean between samples, and of the lane factor |
| `line_ending` | `crlf` | `crlf` or `lf` |
| `samples`, `sample_pattern` | None, `SMPL-99` | Sample count and shape in the resample mode |

The negative controls do not follow the lane factor. An effect can target an endogenous or a housekeeping code.

### FCS

An FCS dataset writes one FCS 3.1 file per sample, with float data. Events come from a mixture of populations on the arcsinh scale.

| Field | Default | Content |
|---|---|---|
| `channels` | Required | Each channel with `name` (`$PnN`), `label` (`$PnS`) and `range` (`$PnR`) |
| `populations` | Required | Each population with `name`, `weight`, and `means` and `sds` per channel on the arcsinh scale |
| `events` | Required | Quantiles of the event count per file |
| `cofactor` | `150` | Cofactor of the arcsinh scale |
| `weight_cv` | `0.3` | Spread of a population share between files |
| `keywords` | `[]` | Names of other keywords. Each value is the generic text `value` |
| `samples`, `sample_pattern` | None, `SMPL-99` | File count and shape in the resample mode |

The reader accepts a data end one byte too far, as the MACSQuant writes it. A file with several data sets, or with integer data of 24 bits, gives an error, because `flowio` cannot read it. A profile pools events from all files and finds the populations with k-means. A population with less than 10 events is dropped. A channel named `Time` gets increasing values. An effect multiplies the frequency of a population. `sdg validate` reads each file back and assigns each event to the population with the highest likelihood, from the centres and spreads of the config. The time channel and a channel with no spread are not used.

### FASTQ

A FASTQ dataset writes one file, or one pair, per sample. In the model mode it writes one per design sample. It has no feature for an effect.

| Field | Default | Content |
|---|---|---|
| `paired`, `pair_suffixes` | `true`, `[_R1, _R2]` | Pairs, and the file name suffix of each mate |
| `compressed` | `true` | Write `.fastq.gz` with a fixed gzip time |
| `reads` | `10000` | Reads or pairs per sample. A profile records at most 100,000 |
| `read_length`, `length_quantiles` | `150`, None | Read length, and the quantiles of trimmed lengths |
| `quality_means`, `quality_sds` | `[]` | Quality profile per position. A short profile stretches over the read |
| `quality_offset` | `33` | 33 or 64 |
| `header_style`, `index_length`, `index_dual` | `illumina`, `8`, `false` | Header form and index |
| `reference`, `library` | None, `wgs` | A public FASTA path, or None for a random genome; `rnaseq` samples transcripts evenly |
| `insert_mean`, `insert_sd` | `300`, `50` | Fragment length of a pair |
| `gc_share`, `n_share` | `0.41`, `0.0` | GC share of the random genome, and share of `N` bases |

A read gets base errors at the rate of its quality scores. A profile cannot measure the insert size without an alignment, so it writes the defaults and lists them.

## The output folder

| Item | Content |
|---|---|
| Data files | The files and folders that the config names |
| `truth/effects.csv` | Each planted effect at each timepoint, with the applied and the planned fold change |
| `truth/censored.csv` | Each censored value, with its true value and its limit |
| `truth/samples.csv` | Each design sample, with its group, its timepoint and its presence |
| `config.yaml` | A copy of the config, comments included |
| `manifest.json` | The tool version, the seed and a SHA-256 hash of each file |
| `SYNTHETIC_DATA.md` | A notice that the data is synthetic |

The manifest holds no timestamp. A text file, such as a CSV, TSV, FASTQ or RCC file, has the same bytes on a rerun. A binary file, such as an Excel, Parquet or h5ad file, holds the same data, but its bytes can differ.

## Validation

`sdg validate` runs three checks:

1. Each manifest file exists and has its recorded hash.
2. Each format module reads its output back and compares it with the config, such as the header, the row count or the sample count.
3. In the model mode, each row of `truth/effects.csv` is tested with Welch's t-test between the two groups at its timepoint, on the log2 scale. The replicates of a sample are averaged first. For an FCS dataset, the value is the frequency of a population in a file.

A planted effect is found when the test is significant and the sign agrees. A small design can miss a real effect. On a null dataset, the share of significant tests is the false positive rate, which is near the alpha.

## Limits of version 1

`docs/public_data.md` lists the public files that show some of these limits, and `tests/public_data_test.sh` tests them.

- In a table of text only, the header search cannot see where the data starts. So it takes the first row after the comment rows as the header, and a title row or a second header row stays in the table. The names of the first data row still count for the private-column rule.
- The synthetic file writes `note` in the rows above the header. Code that reads the text of these rows, such as the GEO metadata lines that GEOquery reads, does not find the real content.
- An old `.xls` workbook is not read. Save it as `.xlsx` first.
- A column of numbers and many different texts, such as a lab value column, is free text. The synthetic column holds invented words.
- A count matrix reads each numeric column after the gene column as a sample, except a column with a known annotation name such as `Length`. A numeric annotation column with another name reads as a sample.
- A long Olink file must hold each pair of sample and assay. The synthetic file of a file with missing pairs has more rows.
- A free text column writes text cells. A shape of digits gives a number in a CSV file, but a workbook gets a text cell, also when some real cells are numbers.
- An FCS file with several data sets, or with 24-bit integer data, is not read.
- The FASTQ glimpse estimates the read count from the first 10,000 reads. For a gzip file, the estimate is about 15% too low.
- The wide Olink layout was checked with OlinkAnalyze 5.1.0 on Target 96 NPX files only.
- A microarray dataset is a processed matrix. Raw CEL files are not read.
- NanoString GeoMx DCC files are not read.
- The `resample` mode keeps the rank correlation of the numeric pairs that a sheet lists in `correlations` only. It keeps no correlation between a number and a category.
- `sdg describe` reads one table of a CSV, TSV, Excel or Parquet file. It does not read a wide Olink file or a count matrix. To join a group column from another file, merge the tables in a DataFrame and call `describe.groups`.
- The model mode plants an effect between groups of subjects. A condition within a subject, such as a treatment of the samples of one subject, has no model field.
- A key link needs the glimpse or the profile of the two tables in one run. A key column is an identifier column with unique values.
- `sdg compare` pairs tables by position, so the synthetic glimpse must list its files in the order of the real one.
- `sdg relabel` copies CSV, TSV, Excel and Parquet tables only. It does not rename assays, panels, FCS markers, RCC codes or matrix features, because the key does not hold them.
- A numeric column prints each text cell as a token, such as `NA` or `<LOD`. In a table that is not private, a long text in a numeric column, such as a footnote, prints in full. The audit of `sdg report` stopped such a footnote in the public TCGA workbook only because it held a real column name.
