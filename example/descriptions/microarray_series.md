# Microarray series

This is an example of a description that a user gives to the agent. Every name and number is invented.

The customer sent the processed expression matrix of an Affymetrix HG-U133 Plus 2.0 study, platform GPL570. The file is a TSV file with log2 intensities: one row per probe and one column per sample. The first column is `ID_REF`.

There are 10 responders and 10 non-responders, with one array each. The arrays came in two batches, so whole arrays shift a little. For the test data, keep 1,000 probes, and plant a 2-fold rise of the first 3 probes in the responders.
