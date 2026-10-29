# Public data test

`tests/public_data_test.sh` runs the `sdg` chain on public files of real deliveries. The files come from GEO, GDC, cBioPortal, PhysioNet, the UCI repository and the test data of R and Python packages. No file holds data of a customer.

## Run the test

1. Make sure that `curl` and the GitHub CLI `gh` are installed. If LibreOffice `soffice` is installed too, the script adds the case `credit_default_xlsx`.
2. Run `tests/public_data_test.sh` from the repository root.
3. Read the result line of each case. The last line tells if each case gave its expected result.

The script downloads the files into `input/public/`, which git ignores. A second run uses the files on disk. The outputs go to `sdg_output/public_<timestamp>/`, and `inputs.sha256` there holds a SHA-256 hash of each input. To run some cases only, give a part of their names, as in `tests/public_data_test.sh olink nanotube`.

The script runs these steps on each case:

1. `sdg glimpse` on the real files.
2. A check that the glimpse prints a known fact of the structure, such as `samples: 107`. This step is `structure`.
3. `sdg audit` of the glimpse report.
4. `sdg profile`, then `sdg audit` of the config.
5. `sdg check` and `sdg generate`.
6. `sdg glimpse` on the synthetic files, then `sdg compare` of the two reports.
7. `sdg validate` of the synthetic output.

The result of a case is `pass`, or the first step that failed. Each case has an expected result, and the script fails when a result differs from it. So a fix of a known limit shows as a difference too.

## Sources

The script pins each GitHub source to a commit.

| Case | Format | Source | Content |
|---|---|---|---|
| `tcga_cdr_xlsx` | Excel | GDC, supplemental table S1 of the TCGA-CDR paper, Cell 2018 | 8 sheets. The main sheet has 11,160 patients and 34 columns, with `#N/A` cells |
| `cbioportal_brca_tsv` | TSV | cBioPortal datahub, study `brca_tcga_pan_can_atlas_2018` | A clinical patient file with 4 comment lines above the header and 1,084 patients |
| `penguins_raw_csv` | CSV | palmerpenguins package, `penguins_raw.csv` | 344 penguins and 17 columns, with dates, `NA` cells and comments |
| `mimic_patients_csv` | CSV | MIMIC-IV clinical database demo 2.2 on PhysioNet, `hosp/patients.csv.gz` | 100 patients and 6 columns |
| `mimic_admissions_csv` | CSV | MIMIC-IV demo, `hosp/admissions.csv.gz` | 275 admissions and 16 columns, with date and time cells |
| `mimic_labevents_csv` | CSV | MIMIC-IV demo, `hosp/labevents.csv.gz` | 107,727 lab results and 16 columns, with a value column of numbers and text |
| `cervical_cancer_csv` | CSV | UCI, cervical cancer risk factors | 858 patients and 36 columns, with `?` cells |
| `heart_failure_csv` | CSV | UCI, heart failure clinical records | 299 patients and 13 columns |
| `credit_default_xls` | Excel 97 | UCI, default of credit card clients | 30,000 clients under two header rows |
| `credit_default_xlsx` | Excel | The same workbook, saved as `.xlsx` by LibreOffice | The row of codes `X1` to `X23`, then the names, with `SEX` and `AGE` |
| `real_estate_xlsx` | Excel | UCI, real estate valuation | 414 sales and 8 columns |
| `olink_long_csv` | Olink, long CSV | OlinkAnalyze package, `npx_data_long_csv.csv` | 1,000 rows of 4 samples and 906 assays |
| `olink_ext_parquet` | Olink, Parquet | OlinkAnalyze package, `npx_data_ext.parquet` | 1 row of Explore HT |
| `olink_npx1_xlsx` | Olink, wide Excel | OlinkAnalyze package, `npx_data1.xlsx` | 160 samples and 184 assays |
| `olink_npx1_wide_xlsx` | Olink, wide Excel | OlinkAnalyze package, `npx_data1_original.xlsx` | 160 samples and 1,104 assays |
| `gse60450_counts` | Count matrix | GEO GSE60450 | 27,179 genes, a `Length` column and 12 samples |
| `gse10072_microarray` | Microarray | GEO GSE10072, series matrix | 22,283 probes and 107 arrays under the GEO metadata lines |
| `pbmc3k_processed_h5ad` | AnnData | cellxgene example dataset `pbmc3k.h5ad` | 2,638 cells, 1,838 genes and 4 embeddings |
| `pbmc3k_raw_h5ad` | AnnData | scanpy dataset `pbmc3k_raw.h5ad` | 2,700 cells and 32,738 genes |
| `nanotube_rcc` | NanoString | NanoTube package, GEO GSE117751 | 42 RCC files with 608 codes |
| `nanostringqcpro_rcc` | NanoString | NanoStringQCPro package | 25 RCC files with 32 codes |
| `fcs_cytotrol` | FCS | flowWorkspaceData package | 2 CytoTrol files with 12 channels |
| `fcs_<file>` | FCS | fcsparser test set | 13 files from BD, Cytek, Miltenyi, Guava and Partec instruments, and 3 broken files |
| `fastq_pair` | FASTQ | nf-core test-datasets, human `test_1` and `test_2` | 266,736 read pairs, with the `genome.fasta` reference |

## Expected results

Each case that this table does not list must give `pass`.

| Case | Result | Reason |
|---|---|---|
| `tcga_cdr_xlsx` | `compare` | The `Weblink` column of the `TSS_Info` sheet is free text with 11% number cells. A free text column writes text cells only |
| `credit_default_xls` | `glimpse` | `sdg` reads `.xlsx` workbooks only, and it asks for a copy saved as `.xlsx` |
| `olink_long_csv` | `compare` | The file holds 1,000 of the 3,624 pairs of sample and assay. The synthetic file holds each pair |
| `olink_ext_parquet` | `audit_glimpse` | The file has 1 row, so each column is free text. The audit finds the cell `assay` in the fact `assays` of the report |
| `fcs_Cytek_xP5` | `glimpse` | The file has 24-bit integer data, which `flowio` cannot read |
| `fcs_Guava_Muse` | `glimpse` | The file holds several data sets |
| `fcs_corrupted`, `fcs_fake_large_fcs`, `fcs_sample_header` | `glimpse` | The files are broken on purpose. `sdg` gives an error with the file name |

## Faults that the test found

The first run on 2026-10-12 found these faults in version 0.2.0. Version 0.3.0 fixes them.

| Fault | Fix |
|---|---|
| The `CartridgeID` lane attribute of 8 NanoTube files held a person name. The glimpse printed the name as a token of a numeric attribute | A numeric RCC attribute with a text value prints shapes only |
| A TCGA-CDR survival column with 68% `#N/A` cells was free text. A lone number among `#N/A` cells printed as a category level | The numeric share does not count the standard missing tokens |
| The Olink columns `Sample_Type` and `SampleQC` were identifiers, so the synthetic file lost the levels `CONTROL` and `SAMPLE` | A name that ends with `type`, `qc`, `status`, `warning`, `group` or `class` is not an identifier |
| The audit found `#N/A`, free text inside a printed level, and FCS keywords that copy a channel name | The audit skips these values. An identifier or a date inside a printed level is still a finding |
| A filled RCC `Owner` attribute was empty in the synthetic files, and an empty `CartridgeBarcode` was filled | The config records `text_attributes` and `empty` lane attributes |
| The embeddings of an AnnData file listed in another order, and `sdg validate` refused unsorted layers | The glimpse and the check sort the keys |
| `sdg compare` took the share of a missing token over the tokens only, so 1 sub-header cell in 40 was a common item | The share is taken over the filled cells, and a rare missing cell type is a note |
| A token in 1 cell of 17 was absent from about a third of the synthetic sheets, and an empty cell could replace a token | Each share gets its exact number of cells, and tokens and empty cells are drawn together |
| 4 MACSQuant files with a data end one byte too far gave an error, and a broken file gave a traceback | The reader accepts the extra byte, and an unreadable file gives an error with its name |

The second run on 2026-10-12 added 8 CSV and Excel files. It found these faults in version 0.3.0. Version 0.3.1 fixes them.

| Fault | Fix |
|---|---|
| The audit found number cells, such as `152` or `3.0`, in row counts and in the version line `0.3.0` | A value of digits only matches as a quoted item: a level, a token or a name |
| The audit found the MIMIC lab value `6-10` in the date `2026-10-12` of the profile header | A value with digits and no letter matches as a whole token |
| The audit found the cell `999` and the redaction mark `___` in the shapes of the report | The audit skips a value that is its own shape. A date always matches |
| `?` was not a missing token, so a UCI column of numbers and `?` cells was a category | `?` is a standard missing token |
| The `platelets` column was an identifier, because its name holds `plate` | `platelet` does not count as `plate` |

These faults stayed after version 0.3.1:

- The credit workbook has two header rows, `X1` to `X23` and then `LIMIT_BAL`, `SEX`, `AGE` and the other names. A copy saved as `.xlsx` read the first row as the header. So the private-column rule did not see `SEX` and `AGE`, and the glimpse printed their ranges. A title row above the header of a workbook had the same effect. Version 0.4.0 fixes this fault.
- The MIMIC lab `value` column holds numbers and many different texts, so it is free text. The synthetic column holds invented words, and a numeric conversion of it gives missing values. This fault is not fixed.

The third run on 2026-10-13 tested version 0.4.0, which adds a search for the header row. These faults of version 0.3.1 are fixed:

| Fault | Fix |
|---|---|
| The credit workbook read its row of codes as the header, and the glimpse printed the range of `AGE` | The header search takes the names row. The table is private, and the glimpse prints counts only |
| The cBioPortal file read its 4 comment lines as data rows | The comment rows are the preamble. The header is on row 5 |
| The GEO series matrix read its metadata lines as the table, with 1 sample | The `!` lines above the table are the preamble, and the end line is the footer. The matrix has 107 samples |
| The GSE60450 count table read its `Length` column as a 13th sample | A numeric column named `Length`, `Start` or another annotation name is an annotation column. The profile records its deciles |
| Two TCGA-CDR sheets read a row of group names as the header | The header search skips a row that fills half of the width or less |
| A private workbook table wrote its integer `ID` cells as text | An identifier column of integers in a workbook keeps number cells |

In the first version of the header search, a single `01` cell of a text column confirmed a header row. The synthetic file writes invented words in a text column, so the TCGA-CDR TSS sheet read with another header in the synthetic file. Now the kind of a whole column confirms the header.

The test also found that the FASTQ glimpse gives about 228,000 reads for a file of 266,736 reads. The glimpse estimates the count from the first 10,000 reads, and the gzip reader reads ahead of them. This fault is not fixed, because a profile records 100,000 reads at most.
