# Plate reader export

This is an example of a description that a user gives to the agent. Every name and number is invented.

The customer sent one plate reader export as a CSV file. The file uses a semicolon as the delimiter.

There are 24 samples, and each sample is read at 3 dilutions, so the file has 72 rows. Two rows are fully empty, because two wells were not read.

These are the columns, in file order:

1. `Sample ID` holds a sample code such as `PLT-0001`. Each sample has 3 rows.
2. `Dilution` holds `neat`, `1:10` or `1:100 `. The third level has a trailing space, as in the export.
3. `OD450`, `OD620`, `Corrected` and `Above blank` hold optical densities with three decimals. About 10% of these cells hold `NA`.
4. `Above blank` is often zero. About a third of its values are exactly 0.
5. `Measured` holds `TRUE` or `FALSE`.
