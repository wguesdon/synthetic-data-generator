# Bulk RNA-seq count matrix

This is an example of a description that a user gives to the agent. Every name and number is invented.

The customer sent one count matrix from a bulk RNA-seq study, as a TSV file. The rows are 2,000 genes and the columns are samples. The first column is `gene_id`, and the second column `gene_name` holds the gene symbol.

There are 12 patients: 6 treated and 6 untreated. Each patient has one sample before treatment and one after, so the matrix has 24 sample columns.

The analysis code runs DESeq2. For the test data, plant a 3-fold rise of the first 5 genes in the treated patients after treatment.
