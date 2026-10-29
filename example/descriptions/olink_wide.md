# Olink NPX file in the wide layout

This is an example of a description that a user gives to the agent. Every name and number is invented, the Olink IDs and UniProt IDs too.

The customer sent one Olink NPX file from NPX Manager in the wide Excel layout. The sheet name is `NPX Data`. The first cell holds the project name, and the second cell holds `NPX Manager 2.1.0`. The cell under the project name holds `NPX Data`.

The file has one panel, `Olink Target 96 Inflammation`, with its 92 assays. The analysis uses IL6, TNF and CCL2. The names of the other 89 assays are not important. The top rows are `Panel`, `Assay`, `Uniprot ID` and `OlinkID`. After the assay columns come one `Plate ID` column and one `QC Warning` column. The bottom rows are `LOD`, `Missing Data freq.` and `Normalization`. Normalization is always `Intensity`.

There are 28 patients and 20 healthy donors, with one sample each, so 48 samples ran on 1 plate. About 3% of the samples have a QC warning.

The analysis code reads the file with `OlinkAnalyze::read_NPX()`. For the test data, plant a 2-fold rise of IL6 in the patients.
