# Comparison with FakeDataR and Faker

This document compares `sdg` 0.8.0 with the R package FakeDataR 0.2.2 and the Python package Faker 40.43.0. The question is whether `sdg` is inferior to either tool. The run took place on 2026-10-28, on 5 public tables, with the same seed for each tool. The sections up to [Ease of use](#ease-of-use) give the result of version 0.8.0. The sections [Changes in version 0.9.0](#changes-in-version-090) and [Changes in version 0.10.0](#changes-in-version-0100) give the later runs. The section [Functionality](#functionality) counts the capabilities of each tool, and the section [Person values on real files](#person-values-on-real-files) tests them on public files. The section [Version 0.13.0](#version-0130) gives the last run.

## Result

`sdg` 0.13.0 passes each of the 62 capability checks. FakeDataR passes 22 and Faker passes 19, and each check that one of them passes, `sdg` passes too. Versions 0.9.0 to 0.13.0 close the four points below. The rest of this section gives the result of version 0.8.0.

`sdg` 0.8.0 is not inferior on privacy, on group differences, on the count of formats or on the checks after the generation. It is inferior on four points:

- A table with a private column, such as `age` or `sex`, loses the shape of its values. 4 of the 5 test tables are private.
- A text column, such as a name, a ticket or a cabin, gets invented words. Faker writes real-looking names.
- `sdg` cannot write Parquet for a plain table, and it cannot read a database schema. FakeDataR can do both.
- FakeDataR works in one R function call. `sdg` has no R interface, so an R user calls the `sdg` command line.

| Criterion | Against FakeDataR | Against Faker |
|---|---|---|
| Privacy | Better. `sdg` copied 3 rare real values of 3,768. FakeDataR copied 486 at the `low` level and 1,355 at the `high` level | Equal or better. Faker reads no data. The Faker code of this run copied 15 rare real values |
| Generic names | Better. The FakeDataR bundle of the `medium` and `high` levels holds each real column name in its RDS file | Not applicable. Faker has no generic mode |
| Fidelity of a table with no private column | Better, on the MIMIC admissions table and with the rule off | Equal or better |
| Fidelity of a table with a private column | Worse, by design. 4 of the 5 tables are private | Worse, by design |
| Group difference | Better. Only the `sdg` model mode keeps a group difference. The penguin table is private, so this run needed the rule off | Better, for the same reason |
| Correlation and date order | Equal. No tool keeps them | Equal |
| Code that reads the file | Better. FakeDataR changed 3 numeric columns into text | Worse. The Faker code kept the kind of each column, and `sdg` changed 2 identifier columns into text |
| Person values, such as names | Equal. FakeDataR uses 8 first names | Worse. Faker writes real-looking names in many locales |
| Format coverage | Better on formats. Worse on Parquet output and on a database schema | Better |
| Ease of use | Worse for an R user. FakeDataR needs one R call | Better. Faker needs code for each column |

The section [Gaps of sdg](#gaps-of-sdg) gives the options and a recommendation for each gap.

## The tools

| Item | sdg 0.8.0 | FakeDataR 0.2.2 | Faker 40.43.0 |
|---|---|---|---|
| Purpose | Copy the structure of a delivery from an audited report or a config | Copy the structure of an R data frame | Make single fake values, such as names and addresses |
| Input | Real files on the secure machine, or a pasted report | An R data frame, or a DBI table schema with no rows | None |
| What leaves the secure machine | A text report or a config, after an audit | The fake data as CSV and RDS, a JSON schema and `README_FOR_LLM.txt` | The output of the code that the user writes |
| Numbers | 11 rounded quantiles of each column | `range` draws a uniform number between the real minimum and maximum. `distribution` draws real values with replacement | A provider call, such as `pyfloat` or `random_int` |
| Sensitive columns | Rules on the column kind and the column name. A private table records counts only | A regular expression on the column name. The cell values are not examined | Not applicable |

The FakeDataR `level` argument sets three presets.

| Level | Categories | Column names | Numbers |
|---|---|---|---|
| `low` | Real levels with real shares | Real | `range` |
| `medium` | `Category A` to `Category J`, uniform | `var1` to `varN` | `range` |
| `high` | `Category A` to `Category J`, uniform | `var1` to `varN` | `distribution` |

The source code of FakeDataR confirms these facts. The `distribution` mode calls `sample(vals, n, replace = TRUE)` on the real values. Each column is drawn on its own, so FakeDataR keeps no correlation.

## Method

### Cases

| Case | File | Rows | Columns | Private for sdg |
|---|---|---|---|---|
| `penguins_raw` | `penguins_raw_csv/penguins_raw.csv` | 344 | 17 | Yes, by the column `Sex` |
| `heart_failure` | `heart_failure_csv/heart_failure_clinical_records_dataset.csv` | 299 | 13 | Yes, by `age`, `sex` and the file name |
| `mimic_patients` | `mimic_patients_csv/patients.csv` | 100 | 6 | Yes, by `gender` and `anchor_age` |
| `mimic_admissions` | `mimic_admissions_csv/admissions.csv` | 275 | 16 | No |
| `titanic` | `titanic_csv/titanic.csv` | 891 | 12 | Yes, by `Sex` and `Age` |

`docs/public_data.md` gives the source of the first four files. The Titanic file comes from the GitHub repository `datasciencedojo/datasets` at commit `4cd38e7`. It is the only file with person names.

### Variants

| Variant | Tool and settings |
|---|---|
| `sdg_real` | `sdg report` and `sdg profile` with the default rules, then `sdg generate` |
| `sdg_generic` | The same commands with `--generic-labels` |
| `sdg_rule_off` | The same commands with the private-column rule switched off. Version 0.8.0 has no option for this. The wrapper `sdg_rule_off.py` exists for this measure only |
| `sdg_model` | The penguin table only. The `sdg describe groups` report gives the ratio of each species against the reference, and each ratio becomes an effect of a model-mode config |
| `fakedatar_low`, `fakedatar_medium`, `fakedatar_high` | `llm_bundle()` at each level, with the default formats CSV and RDS |
| `faker` | Code that picks a Faker provider for each column, from the column name and the real cells |

Each variant writes as many rows as the real table, with seed 42.

These choices give each tool its best input:

- FakeDataR reads the table with `readr`, so the dates become `Date` and `POSIXct` columns. Each whole-number column becomes an integer column, because `readr` reads every number as a double.
- Faker reads no data. So `run_faker.py` plays a Faker user, who looks at each column and writes a provider call. The script keeps the real levels, the real ranges, the real missing shares and the most common shape of a code column. It is the work of this comparison, not a part of Faker.

### Measures

The measures in `scripts/comparison/measure.py` do not import `sdg`, so they are the same for each tool. Columns pair by position, because a generic variant renames them.

| Measure | Content |
|---|---|
| Rare text copied | Text cells that occur once in a real column and that the synthetic column holds. A value that occurs once points to one record |
| Rare number copied | The same for numbers with 5 significant digits or more, which a generator seldom draws by chance |
| Real names shown | Real column names in the files of a generic bundle |
| `sdg audit` findings | The findings of `sdg audit` on each text file that leaves the secure machine |
| Kind match | Columns whose cells have the real kind: integer, number, date, date and time, boolean or text |
| Shape match | Code and date columns whose most common shape is the real one, such as `A99` for a cabin |
| KS statistic | The mean Kolmogorov-Smirnov statistic of the numeric columns, without the identifier columns. A numeric column that is no longer numeric counts as 1 |
| Category distance | The mean total variation distance of the columns with 2 to 20 levels. It pairs the levels by label, or by rank when the labels are generic |
| Group omega squared | The share of the variance of a numeric column between groups, corrected for chance |
| Date order | The share of rows where `admittime` comes before `dischtime`, and `edregtime` before `edouttime` |

### Run the comparison

1. Run `tests/public_data_test.sh` one time to fetch the public files.
2. Run `scripts/comparison/run_all.sh` from the repository root.
3. Read `sdg_output/comparison/run_<timestamp>/summary.md`.

The script runs FakeDataR in the container `docker.io/rocker/r-ver:4.5.1` with Podman. Its CRAN repository is the Posit snapshot of 2025-10-30. The FakeDataR tarball must have the SHA-256 hash in `scripts/comparison/fakedatar_0.2.2.sha256`. Faker runs in its own uv environment, so `pyproject.toml` does not change.

## Privacy results

The counts are the sums over the 5 cases. The total is 2,267 rare text values and 1,501 rare numbers.

| Variant | Rare text copied | Rare numbers copied | Real names in a generic bundle | `sdg audit` findings |
|---|---|---|---|---|
| `sdg_real` | 3 | 0 | Not applicable | 0 |
| `sdg_generic` | 0 | 0 | 0 of 58 | 0 |
| `sdg_rule_off` | 8 | 1 | Not applicable | 56 |
| `fakedatar_low` | 257 | 229 | Not applicable | 870 |
| `fakedatar_medium` | 0 | 1 | 58 of 58 | 90 |
| `fakedatar_high` | 600 | 755 | 58 of 58 | 720 |
| `faker` | 14 | 1 | Not applicable | 50 |

These facts explain the table:

- The 3 rare text values of `sdg_real` are category levels of the MIMIC admissions table with 1 row each, such as `ACUTE HOSPITAL`. Privacy rule 3 keeps the category levels.
- The `high` level of FakeDataR copied 50% of the rare numbers, and 64% to 68% on the penguin and MIMIC tables. It copied the real admission dates and death dates of MIMIC.
- The `low` level of FakeDataR copied 60% of the rare penguin texts, such as the identifier `N100A2` and the free text comments. It copied 345 of the 547 Titanic tickets that occur once.
- The FakeDataR bundle of the `medium` and `high` levels writes the RDS file with the attribute `name_map`. That attribute holds each real column name with its generic name, such as `PassengerId = "var1"`.
- The JSON schema of the `high` level holds the real first and last dates of the penguin eggs, because the schema reads the fake data, and the fake data holds real values.
- The 56 audit findings of `sdg_rule_off` are category levels of private tables. The rule that the variant switches off keeps them out.
- One rare number in 1,500 matches by chance in 3 variants.

## Fidelity results

### Numeric columns

The KS statistic is 0 for identical distributions and 1 for distributions with no overlap.

| Variant | penguins_raw | heart_failure | mimic_patients | mimic_admissions | titanic |
|---|---|---|---|---|---|
| `sdg_real` | 0.862 | 0.848 | 0.660 | 0.000 | 0.785 |
| `sdg_generic` | 0.856 | 0.850 | 0.640 | 0.000 | 0.788 |
| `sdg_rule_off` | 0.058 | 0.049 | 0.100 | 0.000 | 0.035 |
| `sdg_model` | 0.211 | | | | |
| `fakedatar_low` | 0.322 | 0.341 | 0.215 | 0.444 | 0.467 |
| `fakedatar_medium` | 0.305 | 0.341 | 0.215 | 0.447 | 0.468 |
| `fakedatar_high` | 0.198 | 0.109 | 0.075 | 0.018 | 0.017 |
| `faker` | 0.168 | 0.241 | 0.180 | 0.007 | 0.191 |

A private table records counts only, so `sdg profile` writes the quantiles `[0, 100]` for each numeric column. With the rule off, `sdg` has the lowest KS statistic on 3 of 5 tables. The `high` level of FakeDataR is lower on the other 2 tables, because it copies real values.

### Category columns

| Variant | penguins_raw | heart_failure | mimic_patients | mimic_admissions | titanic |
|---|---|---|---|---|---|
| `sdg_real` | 0.041 | 0.966 | 0.000 | 0.006 | 0.643 |
| `sdg_generic` | 0.041 | 0.970 | 0.000 | 0.004 | 0.643 |
| `sdg_rule_off` | 0.000 | 0.006 | 0.000 | 0.006 | 0.015 |
| `sdg_model` | 0.000 | | | | |
| `fakedatar_low` | 0.139 | 0.230 | 0.075 | 0.142 | 0.296 |
| `fakedatar_medium` | 0.278 | 0.230 | 0.020 | 0.386 | 0.374 |
| `fakedatar_high` | 0.288 | 0.023 | 0.030 | 0.330 | 0.093 |
| `faker` | 0.062 | 0.042 | 0.040 | 0.097 | 0.016 |

A private table keeps the shares of its text categories with generic labels. A numeric column of codes, such as `DEATH_EVENT` with 0 and 1, gets the default quantiles, so the synthetic column holds integers from 0 to 100. That causes the high distance of `heart_failure` and `titanic`. FakeDataR draws `range` columns uniformly and its generic categories uniformly, so the shares change.

With the rule off, the missing share of each column is exact in `sdg`. With the default rules, two faults change it on the penguin and Titanic tables. The section [Other faults that the run found](#other-faults-that-the-run-found) gives them. FakeDataR and Faker draw each missing cell at random, and the mean difference of a table is 0.009 at most.

### Group difference, correlation and date order

| Variant | Body mass by species, penguins | Survival by sex, Titanic | Strongest correlation, penguins | Admission before discharge, MIMIC |
|---|---|---|---|---|
| Real data | 0.667 | 0.294 | 0.84 | 1.00 |
| `sdg_real` | 0.00 | 0.00 | -0.04 | 0.52 |
| `sdg_rule_off` | 0.00 | 0.00 | -0.06 | 0.52 |
| `sdg_model` | 0.365 | | 0.35 | |
| `fakedatar_low` | 0.04 | 0.00 | Not numeric | 0.47 |
| `fakedatar_high` | 0.00 | 0.00 | Not numeric | 0.52 |
| `faker` | 0.01 | 0.00 | -0.05 | 0.59 |

The first two columns give omega squared. The strongest correlation is the Spearman correlation of flipper length and body mass. The heart failure table gives the same result for the follow-up time by death. Its real omega squared is 0.275, and each variant gives a value between -0.07 and 0.07.

No tool keeps a correlation or a date order in its default mode. The `sdg` model mode planted the species ratios of the groups report. It gave back about half of the group difference, and part of the correlation that comes from the species. The effects move the values of each group, so the KS statistic of the whole column rises to 0.211. `sdg validate` did not test these effects, because the table has no subject column. FakeDataR wrote the flipper length as text, so it has no correlation to measure.

### Code that reads the file

The 5 tables have 64 columns. 8 code and date columns have one shape in half of their cells or more.

| Variant | Columns with the real kind | Columns with the real shape | Changes |
|---|---|---|---|
| `sdg_real` | 62 of 64 | 6 of 8 | The private tables write identifiers as `SUBJ-001`. The penguin `Sample Number` becomes text. The Titanic cabins become invented words |
| `sdg_rule_off` | 63 of 64 | 7 of 8 | `Sample Number` holds 152 values in the shape `99`, so `sdg` adds a suffix, such as `03_132`. The Titanic cabins become invented words |
| `fakedatar_low` | 61 of 64 | 4 of 8 | The name rule fakes `platelets` as phone numbers, because it holds `tel`. It writes `Flipper Length (mm)` as random letters because of `ip`, and `PassengerId` because of `pass`. Times get microseconds, such as `09:58:33.620866` |
| `fakedatar_high` | 59 of 64 | 6 of 8 | The same name rule. A real time at midnight prints as a date only, so a date and time column holds two formats |
| `faker` | 64 of 64 | 8 of 8 | The Faker code reads the kind and the shape of each column |

### Person values

The Titanic `Name` column shows the difference.

| Tool | Synthetic name |
|---|---|
| `sdg` | Invented words, such as `zw wqsg zfcnpimktmcpgsn tny` |
| FakeDataR | One of 8 first names, such as `Casey` or `Taylor` |
| Faker | A full name, such as `Megan Taylor` |

In a table that is not private, the `sdg` report prints the shapes of the column, such as `AAAAAAA, AA. AAAAAA`. The generator does not use them.

## Format coverage

| Item | sdg 0.8.0 | FakeDataR 0.2.2 | Faker 40.43.0 |
|---|---|---|---|
| Input | CSV, TSV, Excel `.xlsx`, Olink, count matrix, microarray, AnnData, NanoString RCC, FCS, FASTQ and a pandas DataFrame | An R data frame, so each file that R reads. A DBI table schema with no rows | None |
| Output | The same 10 formats. Parquet for a long Olink file only | CSV, RDS and Parquet | Python values. The CLI prints values |
| Several files in one run | Yes, one config for many files and formats | No, one data frame for each call | Not applicable |
| Workbook sheets, title rows and comment rows | Yes | No | No |

## Ease of use

| Item | sdg 0.8.0 | FakeDataR 0.2.2 | Faker 40.43.0 |
|---|---|---|---|
| Interface | CLI, Python functions, and one script file for the secure machine | R functions | Python library, and a CLI for single values |
| Install | `uv add` from the Git repository, Python 3.13 | CRAN | PyPI |
| From a real table to fake data | `sdg profile`, `sdg audit` and `sdg generate`, or `sdg report`, an agent session and `sdg generate` | One call to `llm_bundle()` | Code for each column. The code of this run has 202 lines |
| Checks after the generation | `sdg audit`, `sdg compare` and `sdg validate` | `validate_fake()` | None |
| Reproducibility | Seed, a copy of the config and a SHA-256 manifest | Seed | Seed |
| Back to the real names | `sdg relabel` and the label key | The `name_map` attribute | Not applicable |

## Changes in version 0.9.0

Version 0.9.0 holds the fixes that change no privacy rule of the private table. A second run of the comparison on version 0.9.0 gave these changes. The FakeDataR and Faker results did not change.

| Measure | Version 0.8.0 | Version 0.9.0 |
|---|---|---|
| Mean missing share difference, `sdg_real`, penguins | 0.052 | 0.000 |
| Mean missing share difference, `sdg_real`, Titanic | 0.009 | 0.000 |
| Category distance, `sdg_real`, penguins | 0.041 | 0.000 |
| Columns with the real kind, `sdg_rule_off` | 63 of 64 | 64 of 64 |
| Columns with the real shape, `sdg_rule_off` | 7 of 8 | 8 of 8 |
| Rare real values copied, `sdg_rule_off` | 9 of 3,768 | 12 of 3,768 |
| Rare real values copied, `sdg_real` and `sdg_generic` | 3 and 0 | 3 and 0 |

The 3 new rare values of `sdg_rule_off` come by chance from the Titanic shapes. A cabin shape such as `A99` holds 2,600 values, so a synthetic cabin can equal a real one, such as `C91`. A ticket of 6 random digits can equal a real ticket in the same way. The variants with the default rules print no text shape for a private table, so they did not change.

A first build of version 0.9.0 counted each integer key up from the first number of its width, such as 10000000. Its synthetic MIMIC keys then held the real key 10000032. The release draws the start of each run, and no rare MIMIC number matched in the second run.

With the default rules, `sdg_real` still writes `SUBJ-001` for an identifier of a private table and `[0, 100]` for a number. Gap 1 holds this fault.

## Changes in version 0.10.0

Version 0.10.0 gives privacy rule 2 the narrow form of gap 1. The private columns, such as `age` and `sex`, record counts only. The other columns of a private table record safe facts, with no minimum and no maximum. A third run of the comparison gave these changes for the variants with the default rules.

| Measure | Version 0.9.0 | Version 0.10.0 |
|---|---|---|
| KS statistic, `sdg_real`, the 4 private tables | 0.66 to 0.86 | 0.07 to 0.15 |
| Category distance, `sdg_real`, heart failure | 0.966 | 0.232 |
| Category distance, `sdg_real`, Titanic | 0.643 | 0.005 |
| Columns with the real kind, `sdg_real` | 62 of 64 | 64 of 64 |
| Columns with the real shape, `sdg_real` | 6 of 8 | 7 of 8 |
| Rare real values copied, `sdg_real` and `sdg_generic` | 3 and 0 | 3 and 2 |
| `sdg audit` findings, `sdg_real` and `sdg_generic` | 0 and 0 | 0 and 0 |

On the 4 private tables, Faker gives a KS statistic of 0.17 to 0.24, and FakeDataR gives 0.02 to 0.47. So `sdg` is no longer inferior to Faker on the numbers of a private table, and it is better than the `low` and `medium` levels of FakeDataR. The `high` level of FakeDataR stays lower on 3 of the 4 tables, heart failure, MIMIC patients and Titanic, because it copies real values.

The rest of the heart failure distance comes from 2 columns. `sex` is a private column, so it still gets the quantiles `[0, 100]`. `ejection_fraction` has 17 values, more than the 10 of a code column, so it gets quantiles and not codes. The 2 rare numbers of `sdg_generic` match by chance, as in the FakeDataR and Faker runs.

The Titanic cabins are a text column of a private table, so they still get invented words. No column of a private table keeps a correlation or a group difference.

## Functionality

This section counts the capabilities of each tool. One capability is a task of a user, such as "make real-looking names" or "read a database schema". A count of single functions would favor Faker, which has about 25 provider families.

`scripts/comparison/capabilities.txt` lists 62 capabilities and the check of each. `scripts/comparison/run_capabilities.sh` runs each check on each tool with invented input data. A result is one of these three values:

| Result | Meaning |
|---|---|
| `pass` | A run of the check passed |
| `fail` | A run of the check failed. The note of the run gives the reason |
| `none` | The tool has no function for the capability, so no run exists |

A check passes when any documented setting of the tool passes it, such as one of the three FakeDataR levels. Faker reads no data, so its checks give Faker the facts that a user knows, such as the shares of a category. A user function of general code does not count. The run of 2026-10-28 on version 0.10.0 gave this result.

| Group | sdg | FakeDataR | Faker |
|---|---|---|---|
| Input | 4 of 6 | 3 of 6 | 0 of 6 |
| Values | 11 of 17 | 7 of 17 | 14 of 17 |
| Design | 4 of 7 | 0 of 7 | 0 of 7 |
| Privacy | 7 of 9 | 4 of 9 | 1 of 9 |
| Output | 6 of 7 | 5 of 7 | 0 of 7 |
| Formats | 7 of 7 | 0 of 7 | 0 of 7 |
| Workflow | 5 of 9 | 3 of 9 | 4 of 9 |
| Total | 44 of 62 | 22 of 62 | 19 of 62 |
| Total without the domain formats | 37 of 55 | 22 of 55 | 19 of 55 |

`sdg` has the most capabilities in 6 of the 7 groups. Faker has more value capabilities, because it makes real-looking names, emails, addresses and other values in 90 locales.

| Id | Capability | sdg | FakeDataR | Faker |
|---|---|---|---|---|
| I1 | Make a synthetic copy of a real table with no code for each column | pass | pass | none |
| I2 | Copy several tables or sheets in one run | pass | fail | none |
| I3 | Find the header under title rows and comment rows | pass | none | none |
| I4 | Read a Parquet table | pass | none | none |
| I5 | Copy an in-memory data frame | none | pass | none |
| I6 | Make a table from a database schema with no rows | none | pass | none |
| V1 | Numbers follow the real distribution and copy no real value | pass | fail | none |
| V2 | Categories keep their real shares | pass | pass | pass |
| V3 | The missing share is exact | pass | fail | fail |
| V4 | Missing tokens such as NA or <LOD stay in a numeric column | pass | fail | none |
| V5 | Invented identifiers keep the real shape | pass | fail | pass |
| V6 | Unique values on request | pass | none | pass |
| V7 | Dates keep their spacing from an invented start | pass | fail | pass |
| V8 | Booleans keep a true share | pass | pass | pass |
| V9 | Text keeps a code shape such as A99 | pass | pass | pass |
| V10 | Real-looking person names | none | fail | pass |
| V11 | Real-looking emails | none | pass | pass |
| V12 | Real-looking phone numbers | none | pass | pass |
| V13 | Real-looking postal addresses | none | fail | pass |
| V14 | Values in other locales | none | none | pass |
| V15 | Other value families such as company, job, IBAN and IP address | none | none | pass |
| V16 | The same seed gives the same data | pass | pass | pass |
| V17 | Any row count | pass | pass | pass |
| D1 | Planted group effect with a truth table | pass | none | none |
| D2 | Subjects in groups at timepoints, with missing samples | pass | none | none |
| D3 | Replicates and censoring at a lower limit | pass | none | none |
| D4 | A null dataset for a false positive check | pass | none | none |
| D5 | Correlation between numeric columns | fail | fail | none |
| D6 | Date order between two columns | fail | fail | none |
| D7 | Key link between two tables | fail | fail | none |
| P1 | Find sensitive columns by their names | pass | fail | none |
| P2 | Protect a sensitive column that has a neutral name | pass | pass | none |
| P3 | Generic column names and levels in the synthetic data | pass | pass | none |
| P4 | No real name in a bundle with generic names | pass | fail | none |
| P5 | A key to the real names that renames the real data | pass | none | none |
| P6 | Audit a shareable file against the real data | pass | none | none |
| P7 | No minimum or maximum of a private column in a shareable file | pass | fail | none |
| P8 | Drop sensitive columns | none | pass | none |
| P9 | Replace a sensitive column with real-looking fakes | none | pass | pass |
| O1 | Write CSV or TSV | pass | pass | fail |
| O2 | Write an Excel workbook | pass | fail | none |
| O3 | Write Parquet | pass | pass | none |
| O4 | Write RDS | none | pass | none |
| O5 | Bundle for an LLM with the data, a schema and instructions | pass | pass | none |
| O6 | Compare the synthetic structure with the real structure | pass | pass | none |
| O7 | Manifest with a hash of each file | pass | none | none |
| F1 | Olink NPX file | pass | none | none |
| F2 | Count matrix | pass | none | none |
| F3 | AnnData h5ad file | pass | none | none |
| F4 | Microarray matrix | pass | none | none |
| F5 | NanoString RCC files | pass | none | none |
| F6 | FCS files | pass | none | none |
| F7 | FASTQ files | pass | none | none |
| W1 | Command line | pass | none | pass |
| W2 | Python interface | pass | none | pass |
| W3 | R interface | none | pass | none |
| W4 | Test fixture for pytest | none | none | pass |
| W5 | Add a new value generator as a plugin | none | none | pass |
| W6 | One script file for a machine without the package | pass | none | none |
| W7 | Agent skill that writes the config | pass | none | none |
| W8 | A saved spec file that makes the same data again | pass | pass | none |
| W9 | Normalize input values such as 85% or yes and no | fail | pass | none |

These results need a note:

- FakeDataR does not flag `age` or `sex`, because its name patterns hold neither word. It flags `patient_id` through the word `patient`.
- The FakeDataR bundle of the `medium` level keeps the real names in the RDS file, so P4 fails.
- The Faker check V5 found 1 real identifier in 200, by chance. The check allows 1%.
- No tool keeps a correlation, a date order or a key link, so D5, D6 and D7 fail for each tool that has a function.

### Capabilities that sdg lacks

At least one other tool passes these 15 checks, and `sdg` does not.

| Capability | Other tool | Option | Recommendation |
|---|---|---|---|
| V10 to V15: names, emails, phones, addresses, locales and other values | Faker, and FakeDataR for emails and phones | A `provider` field on a text column, such as `provider: email` and `locale: fr_FR`, through the optional extra `sdg[faker]`. A profile sets the provider from a column name such as `email` | Build it |
| P9: replace a sensitive column with real-looking fakes | FakeDataR and Faker | The same `provider` field | Build it with the Faker extra |
| W5: a plugin value generator | Faker | A provider of the form `module:Class.method`, which adds a user Faker provider, such as `my_wards:WardProvider.ward_code` | Build it with the Faker extra |
| I5: copy an in-memory data frame | FakeDataR | `describe.profile(frame)`, which gives the config of a DataFrame, as `describe.glimpse(frame)` gives its report | Build it |
| P8: drop sensitive columns | FakeDataR | A `--drop` option with a column pattern for the glimpse, the report and the profile | Build it |
| W4: a test fixture for pytest | Faker | A pytest plugin with the fixture `sdg_data`, which generates a config into a temporary folder | Build it |
| W9: read values such as 85% as numbers | FakeDataR | A numeric column with a `suffix` field, so `85%` reads as 85 and the synthetic cell keeps the form `73%` | Build it. It also keeps the form, which FakeDataR does not |
| I6: a table from a database schema with no rows | FakeDataR | `sdg init --from-schema` on a SQLite file or a SQL `CREATE TABLE` text. Each column gets its kind and default values, marked as assumptions | Build it in a second step |
| O4: write RDS | FakeDataR | An `rds` output through the optional extra `sdg[r]` with `pyreadr` | Build it in a second step |
| W3: an R interface | FakeDataR | A small R package in `r/` that calls the command line and reads the output into data frames | Build it in a second step |

These capabilities fail for each tool. A fix makes `sdg` the only tool with them.

| Capability | Option | Recommendation |
|---|---|---|
| D6: date order | A date field `after` that names an earlier date column, with interval quantiles | Build it with the first step |
| D7: key link | A field `link` on an identifier column that draws the keys of another dataset. | Build it with the first step |
| D5: correlation | A rounded rank correlation matrix in the report, and a Gaussian copula in the resample mode. The matrix is a new fact of the report, so it needs a privacy rule and a test | Build it in a second step |

The recommendation is two versions. Version 0.11.0 holds the Faker extra, the data frame profile, `--drop`, the pytest fixture, the percent suffix, the date order and the key link. Version 0.12.0 holds the database schema, RDS, the R package and the correlation. After version 0.11.0, `sdg` has every capability of Faker. After version 0.12.0, it has every capability of FakeDataR. The person value test then put a new version 0.12.0 first, so this plan moved to version 0.13.0.

### Version 0.11.0

Version 0.11.0 adds these capabilities and the key link. A run of the capability checks on version 0.11.0 gave this result.

| Group | sdg | FakeDataR | Faker |
|---|---|---|---|
| Input | 5 of 6 | 3 of 6 | 0 of 6 |
| Values | 17 of 17 | 7 of 17 | 14 of 17 |
| Design | 6 of 7 | 0 of 7 | 0 of 7 |
| Privacy | 9 of 9 | 4 of 9 | 1 of 9 |
| Output | 6 of 7 | 5 of 7 | 0 of 7 |
| Formats | 7 of 7 | 0 of 7 | 0 of 7 |
| Workflow | 8 of 9 | 3 of 9 | 4 of 9 |
| Total | 58 of 62 | 22 of 62 | 19 of 62 |
| Total without the domain formats | 51 of 55 | 22 of 55 | 19 of 55 |

`sdg` passes each check that Faker passes. The checks that `sdg` still fails are I6, O4 and W3, which version 0.13.0 holds, and D5, which no tool passes.

### Version 0.13.0

Version 0.13.0 holds the second step. `sdg init --from-schema` reads a SQLite file or a `CREATE TABLE` text (I6). The `rds` format writes an RDS file through the extra `sdg[r]` (O4). The R package `sdgr` calls `sdg` from R (W3). Privacy rule 10 records the rank correlation of each strong numeric pair, and a Gaussian copula keeps it (D5). A run of the capability checks on version 0.13.0 gave this result.

| Group | sdg | FakeDataR | Faker |
|---|---|---|---|
| Input | 6 of 6 | 3 of 6 | 0 of 6 |
| Values | 17 of 17 | 7 of 17 | 14 of 17 |
| Design | 7 of 7 | 0 of 7 | 0 of 7 |
| Privacy | 9 of 9 | 4 of 9 | 1 of 9 |
| Output | 7 of 7 | 5 of 7 | 0 of 7 |
| Formats | 7 of 7 | 0 of 7 | 0 of 7 |
| Workflow | 9 of 9 | 3 of 9 | 4 of 9 |
| Total | 62 of 62 | 22 of 62 | 19 of 62 |
| Total without the domain formats | 55 of 55 | 22 of 55 | 19 of 55 |

The W3 check installs `sdgr` in a `rocker/r-ver` container. It makes a config from the visits table as a data frame, and `readRDS` reads the RDS file of the O4 check. `R CMD check` of `sdgr` gives the status OK, with 31 tests.

A run of the comparison on the 5 public tables measured the correlations on real files. The strongest pair is the pair of numeric columns with the largest real Spearman correlation. The mean difference is over each pair of numeric columns with 3 values or more.

| Measure | Real data | `sdg_real` | `fakedatar_low` | `faker` |
|---|---|---|---|---|
| Penguins, flipper length and body mass | 0.84 | 0.82 | Not numeric | -0.05 |
| Titanic, `Pclass` and `Fare` | -0.69 | -0.60 | 0.02 | 0.00 |
| Heart failure, `serum_creatinine` and `serum_sodium` | -0.30 | -0.29 | 0.05 | 0.01 |
| Heart failure, follow-up time by death, omega squared | 0.275 | 0.198 | -0.003 | 0.000 |
| Mean correlation difference, penguins | 0 | 0.035 | 0.467 | 0.471 |
| Mean correlation difference, Titanic | 0 | 0.153 | 0.297 | 0.288 |
| Mean correlation difference, heart failure | 0 | 0.112 | 0.104 | 0.125 |
| Mean correlation difference, MIMIC patients | 0 | 0.023 | 0.064 | 0.149 |

`sdg` keeps the strongest pair of each table, and the other tools keep none. The heart failure table is the one case where FakeDataR has a lower mean difference, 0.104 against 0.112. Most of its pairs are weak. A pair under 0.2 or with a private column, such as `age` or `sex`, records no correlation, so each tool draws these pairs with no correlation. The copula also keeps the follow-up time by death, because `DEATH_EVENT` is a numeric code column.

The first run of version 0.13.0 found a fault. A numeric column with exact values, such as the codes 1 to 3 of `Pclass`, drew those values on its own, so the Titanic pair gave 0.03. The release sorts the values of such a column into the rank order of the copula, and a test covers it. The same run found that a sheet with design columns takes no `correlations`, so the model variant of the comparison now leaves the field out.

The copula changes the draws of the correlated columns. So the rare real values that match by chance changed: `sdg_real` holds 8 of 3,768 and `sdg_generic` holds 9, against 3 and 2 in version 0.10.0. The matches are 3 MIMIC category levels, as before, the Titanic fares 7.6292 and 7.7292, the ticket 28424 and cabins such as `C91` and `A10`. Each one comes from a shape or from a quantile with 4 decimals. The Faker code of the run matched 15, and FakeDataR matched 486 at the `low` level.

## Person values on real files

This section tests on real files the claim that `sdg` has every capability of Faker. The datablist customers file has no fixed copy in git, so the test uses the public Synthea sample of April 2020 instead. Synthea writes fully synthetic patients with names, addresses, SSN and passport numbers, and encounters whose `PATIENT` column names a patient. The test also uses the Titanic file.

`scripts/comparison/run_person_values.sh` runs each tool with its default settings. `sdg` profiles the two Synthea tables in one run, so it finds the key link. The Faker code reads each real column and picks a provider, as a Faker user does. FakeDataR runs at the `low` level and at the default `medium` level.

Each value is the share of the synthetic cells that pass the check, so 1.00 is best. For a copy share, 0.00 is best. A shape check passes a cell whose shape is a shape of the real column, so a tool that keeps the mix of real shapes passes, as a tool that writes the most common real shape does. The table gives version 0.11.0.

| Column | Check | sdg | faker | fakedatar_low | fakedatar_medium |
|---|---|---|---|---|---|
| synthea_patients Id | UUID | 0.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients SSN | a real shape, 999-99-9999 | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients DRIVERS | a real shape | 0.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients PASSPORT | a real shape | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients FIRST | looks like a name | 1.00 | 1.00 | 1.00 | 1.00 |
| synthea_patients FIRST | distinct names, share of real | 0.39 | 0.36 | 0.68 | 0.01 |
| synthea_patients LAST | looks like a name | 1.00 | 1.00 | 1.00 | 1.00 |
| synthea_patients ADDRESS | looks like a street address | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients CITY | looks like a name | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients ZIP | a real shape, 99999 | 0.00 | 1.00 | 0.00 | 0.00 |
| synthea_encounters Id | UUID | 0.00 | 1.00 | 0.00 | 0.00 |
| synthea_encounters START | ISO form with Z | 1.00 | 1.00 | 0.00 | 0.00 |
| titanic Name | looks like a name | 1.00 | 1.00 | 1.00 | 1.00 |
| titanic Name | form 'Last, Title. First' | 0.00 | 0.00 | 0.00 | 0.00 |
| titanic Ticket | a real shape | 0.02 | 0.00 | 1.00 | 0.00 |
| titanic Cabin | a real shape | 0.04 | 1.00 | 1.00 | 0.00 |
| synthea_encounters STOP | on or after START | 1.00 | 0.50 | 0.50 | 0.50 |
| synthea_patients DEATHDATE | on or after BIRTHDATE | 1.00 | 0.06 | 0.49 | 0.62 |
| synthea_encounters PATIENT | a key of the synthetic patients | 1.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients Id | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients SSN | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients PASSPORT | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients FIRST | copy share | 0.00 | 0.00 | 1.00 | 0.00 |
| synthea_patients LAST | copy share | 0.00 | 0.00 | 1.00 | 0.00 |
| synthea_patients ADDRESS | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients CITY | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients ZIP | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| titanic Name | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| titanic Ticket | copy share | 0.00 | 0.00 | 1.00 | 0.00 |

These facts explain the table:

- `sdg` and Faker both write real-looking names, addresses, cities and SSN numbers, and neither copies a real value.
- `sdg` alone keeps the date order and the key link. In the Faker and FakeDataR files, about half of the encounters stop before they start, and no encounter names a synthetic patient.
- Faker gives more real-looking values in 5 checks. The Faker code writes UUIDs for the two `Id` columns, and `sdg` has no UUID generator. The Faker code copies the real shapes of `DRIVERS` and `Cabin`, but `sdg` prints no text shape in a private table. `sdg` reads `ZIP` as a number, so `01013` becomes `1013`.
- No tool writes the Titanic form `Braund, Mr. Owen Harris`, and no tool writes the Synthea passport form `X19277260X`.
- The `low` level of FakeDataR copies each real first name, last name and ticket.

So `sdg` 0.11.0 has every capability of Faker, but on these real files Faker gives the more real-looking value in 5 of 19 checks, and `sdg` in 3.

### Gaps of the person values

| Gap | Option | Trade-off | Recommendation |
|---|---|---|---|
| UUID keys | A UUID identifier: the profile writes `pattern: uuid` when each value is a UUID, and the generator draws random UUIDs | No new dependency. The secure machine needs no change, because a profile only writes the word `uuid` | Build it |
| Text shapes in a private table | Add the shapes of a text column to the safe facts of rule 2 | A shape holds no value, as an identifier shape does, which rule 2 already allows. It changes a privacy rule, so it needs a test | Build it |
| Codes with a leading zero, such as ZIP `01013` | A column of digits where some cells start with 0 reads as a code with its shapes, not as a number | The code keeps its width and its zeros. Its distribution is lost, which a code does not need | Build it |
| The Titanic name form and the Synthea passport form | A code provider gives way to the common shapes, and a name column gets a Faker template of its form | It needs the text shapes of a private table first | Build it with them |

Version 0.12.0 holds the four options and the change to rule 2.

### Version 0.12.0

Version 0.12.0 holds the four options. The UUID pattern, the leading zero codes, the text shapes of a private table, the code shapes and the name templates each have a test. A second run of the person value test gave this result.

| Column | Check | sdg | faker | fakedatar_low | fakedatar_medium |
|---|---|---|---|---|---|
| synthea_patients Id | UUID | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients SSN | a real shape, 999-99-9999 | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients DRIVERS | a real shape | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients PASSPORT | a real shape | 1.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients FIRST | looks like a name | 1.00 | 1.00 | 1.00 | 1.00 |
| synthea_patients FIRST | distinct names, share of real | 0.39 | 0.36 | 0.68 | 0.01 |
| synthea_patients LAST | looks like a name | 1.00 | 1.00 | 1.00 | 1.00 |
| synthea_patients ADDRESS | looks like a street address | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients CITY | looks like a name | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_patients ZIP | a real shape, 99999 | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_encounters Id | UUID | 1.00 | 1.00 | 0.00 | 0.00 |
| synthea_encounters START | ISO form with Z | 1.00 | 1.00 | 0.00 | 0.00 |
| titanic Name | looks like a name | 1.00 | 1.00 | 1.00 | 1.00 |
| titanic Name | form 'Last, Title. First' | 0.66 | 0.00 | 0.00 | 0.00 |
| titanic Ticket | a real shape | 0.86 | 0.00 | 1.00 | 0.00 |
| titanic Cabin | a real shape | 1.00 | 1.00 | 1.00 | 0.00 |
| synthea_encounters STOP | on or after START | 1.00 | 0.50 | 0.50 | 0.50 |
| synthea_patients DEATHDATE | on or after BIRTHDATE | 1.00 | 0.06 | 0.49 | 0.62 |
| synthea_encounters PATIENT | a key of the synthetic patients | 1.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients Id | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients SSN | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients PASSPORT | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients FIRST | copy share | 0.00 | 0.00 | 1.00 | 0.00 |
| synthea_patients LAST | copy share | 0.00 | 0.00 | 1.00 | 0.00 |
| synthea_patients ADDRESS | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients CITY | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| synthea_patients ZIP | copy share | 0.08 | 0.00 | 0.00 | 0.00 |
| titanic Name | copy share | 0.00 | 0.00 | 0.00 | 0.00 |
| titanic Ticket | copy share | 0.00 | 0.00 | 1.00 | 0.00 |

`sdg` gives a value at least as real-looking as Faker in each of the 19 checks. It gives the better value in 6 of them: the passport form, the Titanic name form, the ticket form, the two date orders and the key link. No check favors Faker. The Titanic name form reaches 0.66, because the Faker title `Miss` has no full stop.

A run on version 0.13.0 gave the same table.

8% of the synthetic ZIP codes equal a real ZIP code by chance. A ZIP code has few values, and the synthetic codes run from a drawn start, so a run can cross real codes. A ZIP code is not unique to one person.

## Gaps of sdg

Each gap below has options and a recommendation.

| Gap | Decision | State |
|---|---|---|
| 1. A private table loses the shape of its values | Option B in a narrow form. The private columns keep counts only | Fixed in version 0.10.0 |
| 2. A text column loses its shape | Option A | Fixed in version 0.9.0 |
| 3. An identifier with mixed shapes becomes text | Option A | Fixed in version 0.9.0 |
| 4. No real-looking person values | The Faker extra | Done in version 0.11.0 |
| 5. Parquet, database schemas and R | Parquet output, and documentation for the other two | Done in version 0.9.0 |
| A date pair field | The field `after` | Done in version 0.11.0 |
| The `NA` level and the empty text cells of the other faults | Fix | Fixed in version 0.9.0 |
| I6, O4 and W3: a database schema, RDS output and an R interface | `sdg init --from-schema`, the `rds` format and the R package `sdgr` | Done in version 0.13.0 |
| D5: correlation between numeric columns | Privacy rule 10 and a Gaussian copula | Done in version 0.13.0 |

### Gap 1: a private table loses the shape of its values

This gap is the largest. 4 of the 5 public tables are private, because a column name holds `age`, `sex` or `gender`. Then each numeric column gets the quantiles `[0, 100]`, each identifier gets the shape `SUBJ-999`, and a column of 0 and 1 holds integers from 0 to 100. Code that fits a logistic model on `DEATH_EVENT` fails on the synthetic data only.

| Option | Effect | Trade-off |
|---|---|---|
| A. Keep the rule | The report shows no value of one person | The synthetic data of most person-level tables does not test the code |
| B. Keep safe facts in a private table | Keep the identifier shapes, the decimals and the codes of a numeric column with 10 values or less. Keep the quantiles from 5% to 95% when the column has 50 values or more | More code works on the synthetic data. Each new fact needs a privacy test and a decision on the limits |
| C. Add an option that names the columns to describe | The user chooses each column | One wrong option shows real values. The audit must accept the option |

The recommendation is B. A private table already keeps the shares of its text categories, so the codes of a numeric column add little. The limits of 10 codes and 50 values are proposals.

### Gap 2: a text column loses its shape

The report prints the shapes of a text column, but the generator writes invented words. The Titanic tickets, cabins and names lose their form, so code such as `cabin.str[0]` reads a random letter.

| Option | Effect | Trade-off |
|---|---|---|
| A. Draw the text from the printed shapes, when the shapes fill half of the cells or more | Codes such as `A99` keep their form | A shape list is longer in the config |
| B. Keep invented words | No change | Code that parses the column fails on the synthetic data only |

The recommendation is A, because the report already holds the shapes.

### Gap 3: an identifier with mixed shapes becomes text

The penguin `Sample Number` holds 1 to 3 digits. The config keeps the most common shape `99`, which holds 100 values, and the column has 152. So `sdg` adds suffixes such as `03_132`, and a column of integers becomes text.

| Option | Effect | Trade-off |
|---|---|---|
| A. Keep each shape with its share | The mix of `9`, `99` and `999` stays | The config holds a short list of shapes |
| B. Add a digit when the shape is too small | The column stays numeric | The shape differs from the real one |

The recommendation is A.

### Gap 4: no real-looking person values

A column of person names gets invented words. Faker writes real-looking names, emails and addresses in many locales.

| Option | Effect | Trade-off |
|---|---|---|
| A. An optional Faker extra, with a `provider` field on a text column, such as `provider: name` | The agent sets the field from the column name | One more dependency on the workstation. The secure script does not change |
| B. A short list of names in `sdg` | No new dependency | Few locales and few kinds of values |
| C. No change | No work | Code that parses names or emails fails on the synthetic data only |

The recommendation is A. Faker invents each value, so it adds no privacy risk.

### Gap 5: Parquet, database schemas and R

| Item | Option | Recommendation |
|---|---|---|
| Parquet output for a plain table | Add `.parquet` to the tabular format. `pyarrow` is already a dependency | Add it |
| A database schema with no rows | A description of the schema to the agent already gives a config | Document this path in the skill |
| An R interface | Call the CLI from R with `system2()`, or write an R wrapper package | Document the `system2()` call |

Version 0.13.0 adds `sdg init --from-schema` and the R package `sdgr`, so the last two items now have a function.

### Limits that all three tools share

Version 0.8.0 kept no correlation, no date order and no link between two tables, as the other tools. Version 0.11.0 adds the date order and the key link, and version 0.13.0 adds the correlation, so these limits now apply to FakeDataR and Faker only. In the MIMIC admissions table, about half of the synthetic rows have a discharge before the admission. These limits do not make `sdg` inferior, but they can break the analysis code.

A join of the synthetic MIMIC admissions to the synthetic patients found each key with `sdg_rule_off`, and 36% to 70% of the keys with FakeDataR and Faker. Both results come by chance. `sdg` 0.8.0 numbered the identifiers from 1 in each file. In version 0.9.0, both files draw the same start, because each config has the same seed, dataset name and column name. FakeDataR and Faker use the same seed and the same key range in both files.

| Option | Effect |
|---|---|
| A date pair field, such as `after: admittime` with interval quantiles | Each discharge comes after its admission |
| A correlation matrix of rounded values in the report, and a Gaussian copula in the resample mode | Numeric columns keep their correlation |
| A key link between two datasets | A join of two synthetic tables finds each key |

The recommendation is the date pair field first, because it is small and analysis code often computes a length of stay.

### Other faults that the run found

- With `--generic-labels` or in a private table, the missing token `NA` of a category column becomes a generic level, such as `level_3`. So the penguin `Comments` column, with 84% `NA` cells, has no missing cell in the synthetic data.
- A private text column gets the lengths 0 to 20, so 5% of the synthetic Titanic names are empty. The real column has no empty cell.
- A category level with 1 row prints in the report, such as a free text comment of the penguin table when the rule is off. A fold of rare levels into one level would close this point.
- `sdg validate` cannot test the effects of a wide table with no subject column.
- The name `PassengerId` is not an identifier, because the rule needs `id` as a separate word, after `_` or at the start of the name. The table is private, so the value range does not print.

## Weak points of the other tools

| Tool | Weak point |
|---|---|
| FakeDataR | The `high` level copies real values, and its schema shows the real minimum and maximum |
| FakeDataR | The RDS file of a generic bundle holds the real column names |
| FakeDataR | The name rule matches parts of words, so `platelets`, `Flipper Length` and `PassengerId` become fake phone numbers or random letters |
| FakeDataR | The `range` mode draws uniformly, so the distribution changes |
| FakeDataR | A generic category column has 10 levels at most, with equal shares |
| FakeDataR | `README_FOR_LLM.txt` holds the output path that the user gave, which can name a project |
| Faker | Faker reads no data, so the user writes code for each column, and that code decides the privacy |

## Limits of this comparison

- The run uses 5 CSV tables. FakeDataR and Faker do not read the other formats of `sdg`.
- The run uses one seed. The measures are shares over hundreds of cells, but a second seed would give slightly different numbers.
- The Faker result depends on the code of `run_faker.py`. Other Faker code gives other results.
- The `sdg_rule_off` and `sdg_model` variants use a wrapper that switches off a privacy rule. A user of version 0.8.0 cannot do this.
