# Lab Parquet export

This is an example of a description that a user gives to the agent. Every name and number is invented.

The customer sent one table of lab results as a Parquet file. The table comes from a data warehouse, so each column has a type and an empty cell is a null.

The file has 200 rows. These are the columns, in file order:

1. `record_id` holds an integer with 5 digits. Each row has its own value.
2. `site` holds `north`, `south` or `east`, in the shares 0.5, 0.3 and 0.2.
3. `ward_code` holds a ward code. Most codes are one letter and two digits, such as `K12`. About a fifth are one letter and three digits.
4. `crp_mg_l` holds a CRP value in mg/L between 0.3 and 180, with one decimal. About 8% of the cells are null.
5. `result_date` holds the date of the result, over about 90 days.
6. `collected_at` holds the date and the time of the sample collection.
7. `fasting` holds true or false. About a third of the values are true.
