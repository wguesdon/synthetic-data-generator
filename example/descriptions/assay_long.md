# Immunoassay panel in the long layout

This is an example of a description that a user gives to the agent. Every name and number is invented.

The study is a two-arm trial with 20 patients. There are 12 patients in `arm_a`, the treated arm, and 8 in `arm_b`, the control arm.

Each patient has three visits: `baseline`, `week_6` and `week_12`. About 10% of the visits after baseline are missed.

An immunoassay panel of 5 analytes measures each sample in duplicate wells, in pg/mL. The file is a CSV file with one row per patient, visit, well and analyte. The columns are `Patient ID`, `Arm`, `Visit`, `Well`, `Analyte` and `Concentration`.

About 1% of the values fall below the lower limit of quantification. The file shows them as `<LLOQ`.

In `arm_a`, IL6 doubles at week 12, and IL10 rises by 10% at week 6.
