# Flow cytometry FCS files

This is an example of a description that a user gives to the agent. Every name and number is invented.

The customer sent one folder of FCS 3.1 files from a T cell panel, one file per sample. Each file has about 10,000 events and the channels `FSC-A`, `SSC-A`, `FITC-A` (CD3), `PE-A` (CD4), `APC-A` (CD8) and `Time`.

There are 10 treated and 10 control subjects, one file each. The analysis code gates lymphocytes, then CD3+ T cells, then the CD4+ and CD8+ subsets.

For the test data, plant a 2-fold rise of the CD8+ population in the treated subjects.
