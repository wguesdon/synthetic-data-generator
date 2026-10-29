# Olink NPX file

This is an example of a description that a user gives to the agent. Every name and number is invented, the Olink IDs and UniProt IDs too.

The customer sent one Olink NPX file in the long layout, as a CSV file with a semicolon delimiter. It has one row per sample and assay.

There are 20 subjects, 12 treated and 8 on placebo, each at `baseline` and `week_4`, so 40 samples ran on 2 plates. About 5% of the samples have a QC warning.

The panel has 4 assays: IL6, TNF, CXCL8 and IL10. The columns are `SampleID`, `Index`, `OlinkID`, `UniProt`, `Assay`, `MissingFreq`, `Panel`, `PlateID`, `QC_Warning`, `LOD`, `NPX` and `Normalization`. Normalization is always `Intensity`.

For the test data, plant a 2-fold rise of IL6 at week 4 in the treated subjects.
