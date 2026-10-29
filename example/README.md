# Examples

Each example pairs a description in `descriptions/` with the config in `configs/` that the sdg workflow makes from it. Every name and number is invented. The examples are a regression set for the agent workflow: an agent that reads a description must make a config that passes `sdg check`.

| Example | Format | Mode | Content |
|---|---|---|---|
| `plate_export` | `csv` | `resample` | One plate reader export of 24 samples with a semicolon delimiter, NA tokens, exact zeros and a level with a trailing space |
| `rnaseq_counts` | `counts` | `model` | A bulk count matrix of 2,000 genes, 12 patients at two timepoints, and five planted genes |
| `single_cell` | `h5ad` | `resample` | An AnnData file of 2,000 cells with obs columns, a counts layer, embeddings and 5 clusters |
| `microarray_series` | `microarray` | `model` | A GPL570 matrix of 1,000 probes, 20 arrays with a batch shift, and three planted probes |
| `olink_npx` | `olink` | `model` | An NPX file in the long layout: 40 samples on 2 plates, 4 assays, QC warnings and one planted assay |
| `olink_wide` | `olink` | `model` | An NPX Manager file in the wide Excel layout: 48 samples, one Target 96 panel of 92 assays, QC warnings and one planted assay |
| `nanostring_rcc` | `nanostring` | `model` | A folder of 16 RCC files from a custom code set with controls, and one planted gene |
| `fcs_panel` | `fcs` | `model` | A folder of 20 FCS files from a T cell panel with five populations and one planted population |
| `rnaseq_fastq` | `fastq` | `resample` | Paired-end gzip FASTQ files of 4 samples with Illumina headers, from a random genome |
| `minimal_tabular` | `excel` | `resample` | One workbook with one column of each kind. It has no description |
| `lab_rds` | `rds` | `resample` | One lab table as an RDS file of an R data frame. It needs the extra `sdg[r]` and has no description |
| `lab_parquet` | `parquet` | `resample` | One lab table as a Parquet file, with typed columns, null cells, ward codes of two shapes and a date and time column |
| `assay_long` | `csv` | `model` | A two-arm trial with three visits, an immunoassay panel in the long layout, duplicate wells, LLOQ censoring and two planted effects |
| `model_study` | `csv` | `model` | A two-arm study in the wide layout with one planted effect. It has no description |

This command makes the data of one example:

```bash
uv run sdg generate example/configs/plate_export.yaml -o sdg_output/plate_export/data
```
