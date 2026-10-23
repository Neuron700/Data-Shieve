# Quality rules and scoring

Formula version: **1.0**

The score is computed at analysis time and stored with the run. The UI reads the stored score and its contributions; it does not recalculate a different number later.

## Rules

| Rule | Name | Default severity | What it checks |
| --- | --- | --- | --- |
| DS001 | Missing values | Medium | Missing or empty cells by column. |
| DS002 | Duplicate rows | Medium | Exact duplicate records. |
| DS003 | Duplicate column names | Medium | Headers that conflict after normalization. |
| DS004 | Whitespace issues | Low | Leading or trailing whitespace in text values. |
| DS005 | Inconsistent types | Medium | Values that do not match the column's dominant type. |
| DS006 | Inconsistent categories | Low | Category spelling or case variants. |
| DS007 | Date format issues | Medium | Unparseable or inconsistent date representations. |
| DS008 | Potential outliers | Info | Values outside 1.5 x IQR fences. |
| DS009 | Potential sensitive data | High | Heuristic email, phone, ID, or card patterns. |
| DS010 | Constant/near-constant columns | Low | Columns with very low variation. |

Every finding stores the rule ID, severity, issue type, affected count, explanation, recommendation, and up to 20 sample row positions. Finding status is separate from data; marking a finding reviewed does not alter the dataset.

## Dataset score

```text
score = round(100 - missing_penalty - duplicate_penalty - structural_penalty)
```

- Missing penalty: `min(30, 30 * 2 * missing_cells / total_cells)`.
- Duplicate penalty: `min(15, 15 * 2 * duplicate_rows / row_count)`.
- Structural penalty: sum of per-rule deductions, capped at 40 points.
- A triggered structural rule contributes at least 25% of its weight, scaled by affected-row share.
- DS003 scales by affected header share instead of row share.
- DS001 and DS002 are excluded from structural deductions because they already have dedicated penalties.
- DS008 has weight 0 and never reduces the score.

The final score is clamped to 0-100 and rounded to the nearest integer.

### Structural weights

| Rule | Weight |
| --- | --- |
| DS003 | 4.0 |
| DS004 | 2.0 |
| DS005 | 5.0 |
| DS006 | 2.0 |
| DS007 | 3.0 |
| DS008 | 0.0 |
| DS009 | 3.0 |
| DS010 | 1.5 |

Structural deductions are capped at 40 points in total.

### Bands

| Band | Range |
| --- | --- |
| Strong | 90-100 |
| Good | 75-89 |
| Needs Review | 50-74 |
| Poor | 0-49 |

## Column score

Each column receives a display score from 0-100:

```text
column_score = round(100 - null_penalty - finding_penalty)
```

- Null penalty: `min(45, 80 * null_count / row_count)`.
- Finding penalty: weighted by DS004, DS005, DS006, DS007, DS009, and DS010, capped at 45 points.
- DS001, DS002, DS003, and DS008 do not contribute to the column finding penalty.

Column scores are explanatory indicators. They are not used to compute the dataset score.

## Limitations

- Rules describe structural and heuristic patterns, not semantic correctness.
- Business importance is not inferred. Every column is assessed by affected-row share.
- The sensitive-data rule is heuristic and can miss personal data.
- Masking and pseudonymization reduce exposure but are not formal anonymization guarantees.
- Scores are comparable within DataSieve formula version 1.0, not across unrelated products.
