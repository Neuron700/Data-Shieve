# Demo data

All values are fictional. Emails use the reserved `example.com` domain and phone numbers are not real.

- `sample_customers.csv` is designed to trigger several quality rules: a duplicate row, missing amount, leading/trailing whitespace, mixed date formats, category casing variants, a constant region column, an outlier, and fake sensitive patterns.
- `sample_workbook.xlsx` is a two-sheet workbook (`Customers` and `Orders`) for testing the sheet-selection step.

Suggested walkthrough:

1. Create a project.
2. Upload `sample_customers.csv`.
3. Open the dataset and review the profile and findings.
4. Preview a cleaning operation such as trim whitespace or mask email.
5. Apply it and confirm that v2 exists while v1 still opens unchanged.
6. Download the CSV, XLSX, profile JSON, quality report, and cleaning summary from Reports.
7. Upload `sample_workbook.xlsx` and choose the `Orders` sheet to confirm the workbook flow.
