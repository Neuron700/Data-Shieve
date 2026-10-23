# Architecture

## Request flow

DataSieve is a server-rendered Django application. Pages are normal Django templates; HTMX is used only for the findings result list, and Chart.js renders the column-quality chart on the profile page.

```text
Browser
  -> Django URL/view
  -> owner-scoped queryset
  -> service layer (parsing, analysis, rules, cleaning, export)
  -> private storage + SQLite/PostgreSQL
  -> template or authenticated file response
```

There is no separate SPA, API server, or third-party profiling call.

## Application boundaries

| App | Responsibility |
| --- | --- |
| `accounts` | Registration, authentication, profile, password change. |
| `projects` | Projects, dashboard aggregation, navigation context, UI template filters. |
| `datasets` | Upload flow, version records, private storage boundary, preview redaction, exports. |
| `profiling` | CSV/XLSX parsing, type inference, profiling, dataset metrics, analysis orchestration. |
| `quality` | Deterministic DS001-DS010 rules, score calculation, finding status. |
| `cleaning` | Pure cleaning operations, preview/apply orchestration, version creation. |
| `reports` | CSV/XLSX data exports and profile/quality/cleaning reports. |
| `audit` | Safe event records that exclude raw cell content. |

## Data model

```text
Project
  -> Dataset
       -> DatasetVersion (original, cleaned)
            -> AnalysisRun
                 -> ColumnProfile
                 -> QualityIssue
       -> CleaningJob
            -> source DatasetVersion
            -> optional output DatasetVersion
```

A `DatasetVersion` is immutable after creation. The original file is written once. Cleaning creates a new file and a new version row with `parent_version` pointing at the source. Deletion of a project cascades to datasets and their files through application code; version rows themselves are not overwritten in normal use.

## Upload boundaries

`apps/datasets/services/storage.py` generates all storage paths. Uploaded file names never become paths. `resolve_private_path()` resolves candidates beneath `PRIVATE_MEDIA_ROOT` and rejects traversal.

Temporary uploads are inspected before promotion. Multi-sheet workbooks and ambiguous CSV delimiters require confirmation. The upload page removes stale temporary files opportunistically; failed inspection now deletes the temporary file immediately.

## Analysis

`analyze_version()` is synchronous. It parses the version, computes dataset metrics, builds per-column profiles, applies the rules, calculates the score, and writes the run. Failures are stored on the run rather than exposed as raw parser exceptions. The UI can retry analysis for a version.

Sensitive values are not copied into findings. Rule explanations contain counts, column names, ratios, and row positions.

## Cleaning

`build_cleaning_preview()` applies a pure operation to an in-memory frame. It stores:

- row/column counts before and after;
- changed-cell, affected-row, and dropped-row counts;
- up to eight redacted change examples;
- column renames;
- warnings;
- safe operation configuration without salts or raw category mappings.

`apply_cleaning_job()` re-applies the operation to the source version inside a transaction, writes a new file, creates the cleaned version, marks the job applied, and triggers analysis. A per-job salt is retained in the job's operation config for consistent pseudonyms, but it is excluded from previews.

## Privacy

Preview redaction is an interface safeguard, not a transformation of stored source data. It is implemented in `apps/datasets/services/privacy.py`:

- columns with sensitive-looking names are fully redacted;
- emails are partially masked;
- phone-like, NIK-like, and long-number patterns are masked inside text;
- long text is truncated.

Cleaning examples use the same helpers, with additional masking for `mask_identifier` before-values.

## CSS and JavaScript

`static/css/input.css` is the source of truth. Tailwind is used as a compiler/wrapper, while most component styling is hand-written in the input file. Run `npm run build:css` after editing it.

`static/js/app.js` provides progressive enhancement for the mobile sidebar, toasts, upload drop zone, automatic version switching, cleaning field panels, and the Chart.js column chart. The application remains usable without HTMX; HTMX only refreshes the findings list.
