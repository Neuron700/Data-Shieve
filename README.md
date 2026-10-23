# DataSieve

DataSieve is a local-first data-quality workspace for CSV and XLSX files. It profiles tabular data, runs ten deterministic quality rules, and creates cleaned versions without modifying the original upload.

The product is intentionally explicit: every finding has a rule ID, severity, affected count, sample row positions, an explanation, and a recommendation. The quality score is a traceable product indicator, not a certification or an AI judgement.

## What it does

- Imports CSV and XLSX into private storage with a 20 MB default cap.
- Detects encoding, delimiter, headers, workbook sheets, and column types.
- Profiles rows, columns, missing values, uniqueness, distributions, and patterns.
- Runs DS001-DS010: missing values, duplicate rows, duplicate headers, whitespace, inconsistent types, category variants, date formats, potential outliers, potential sensitive data, and constant columns.
- Scores quality with published weights and stores the formula version with each analysis.
- Tracks finding status without changing data.
- Previews cleaning operations with cell/row impact before applying them.
- Writes each applied cleaning operation as a new immutable version.
- Exports data as CSV/XLSX and reports as JSON, CSV, or Markdown.

## Stack

- Python 3.14, Django 6.1
- pandas 3.1, numpy 2.5, openpyxl 3.1
- SQLite by default; PostgreSQL supported through `DB_ENGINE=postgres`
- Tailwind CSS 4.1 for the compiled stylesheet
- HTMX 2 and Chart.js served from `static/vendor/`

## Quick start

Use the project virtual environment. Do not rely on the system Python.

```powershell
cd D:\MIRAGE\DJANGO
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
npm install
npm run build:css
Copy-Item .env.example .env
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py runserver
```

Open `http://127.0.0.1:8000/`, create an account, create a project, and upload `examples/sample_customers.csv`.

### Environment

`config/settings/base.py` reads `.env`. The important settings are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | development key | Must be replaced in production. |
| `DJANGO_DEBUG` | `0` | Base default only; development forces debug on and production forces it off. |
| `DJANGO_ALLOWED_HOSTS` | localhost values | Comma-separated production hosts. |
| `DB_ENGINE` | `sqlite` | Use `postgres` for PostgreSQL. |
| `PRIVATE_MEDIA_ROOT` | `private_uploads/` | Private source and version storage. Never serve this directory. |
| `MAX_UPLOAD_SIZE` | `20971520` | Upload cap in bytes. |
| `PREVIEW_ROW_LIMIT` | `100` | Rows shown in the interface preview. |
| `TEMP_UPLOAD_MAX_AGE_SECONDS` | `86400` | Lifetime for temporary inspection uploads. |

## Testing and checks

```powershell
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe -m pytest -q
npm run build:css
```

The test suite uses an in-memory SQLite database and a temporary private-media directory. It covers privacy previews, upload confirmation, sheet selection, cleaning application, report downloads, owner isolation, and primary page rendering.

## How the workflow works

1. A file is written to `TEMP_UPLOAD_ROOT` for inspection. If a workbook has multiple sheets or a CSV delimiter is ambiguous, the user confirms the choice.
2. The inspected file is promoted to `DATASET_STORAGE_ROOT` and a version-1 original record is created.
3. Analysis stores dataset metrics, column profiles, rule findings, score contributions, and the formula version.
4. Cleaning creates a preview job. The preview is stored with redacted examples and impact counts. Nothing is written to the dataset version yet.
5. Applying a preview writes a new cleaned version, links it to the source version, and re-runs analysis. The original file and original version remain unchanged.
6. Reports are generated from stored metadata and the selected version, not from hidden raw values.

## Security and privacy model

- Every project, dataset, version, issue, cleaning job, and report query is scoped to the signed-in owner.
- Uploaded files and generated versions live under `PRIVATE_MEDIA_ROOT`, outside the static directory. The application streams downloads through authenticated views.
- Previews use `redact_preview()` from `apps/datasets/services/privacy.py`. Sensitive column names are redacted in full; email, phone, NIK, long-number, and similar patterns are masked even in ordinary text.
- Cleaning previews never store the raw mapping values or the pseudonymization salt in the safe configuration.
- The parser never evaluates formulas, macros, or cell content as code.
- CSV/XLSX exports prefix formula-leading text so spreadsheet software does not evaluate untrusted cells.
- Masking and pseudonymization are risk reductions, not claims of legal anonymization. The limitations are repeated in the interface.

## Project layout

```text
apps/
  accounts/       sign-up, login, profile, password change
  projects/       projects, dashboard, navigation, UI template filters
  datasets/       uploads, versions, parsing boundaries, privacy, downloads
  profiling/      parser, inference, profiles, analysis orchestration
  quality/        DS001-DS010, scoring, findings workflow
  cleaning/       operations, preview/apply engine, version creation
  reports/        CSV/XLSX exports and JSON/CSV/Markdown reports
  audit/          safe audit events
config/           settings, URLs, WSGI/ASGI
templates/        Django templates and shared partials
static/css/       input.css and compiled app.css
static/js/        interface behaviour
static/vendor/    HTMX and Chart.js
tests/            pytest-django coverage
examples/         safe demo CSV and XLSX files
docs/             architecture, rules, and operations notes
```

## Deployment notes

Use `config.settings.production` and set a real `DJANGO_SECRET_KEY` plus explicit `DJANGO_ALLOWED_HOSTS`.

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.production"
.venv\Scripts\python.exe manage.py collectstatic --noinput
.venv\Scripts\python.exe manage.py migrate
```

Run the WSGI application with your process manager, and serve `staticfiles/` through a reverse proxy. Do not expose `PRIVATE_MEDIA_ROOT`; authenticated report and export views are the only download path. For PostgreSQL, set the `POSTGRES_*` variables from `.env.example`.

## Known limitations

- Analysis runs synchronously in the request cycle. Very large files are bounded by the 20 MB upload cap but may still take noticeable time.
- Sensitive-data detection is heuristic and can miss personal data or flag harmless values.
- Rules describe structural patterns; they do not prove business correctness.
- The quality score is comparable within this product and formula version, not across unrelated tools.
- There is no collaboration, background worker, or scheduled cleanup service in this version. Temporary uploads are cleaned opportunistically when the upload page is visited.

## Demo files

`examples/sample_customers.csv` contains deliberate missing values, a duplicate row, whitespace, mixed date formats, category variants, a constant region column, an outlier, and fake sensitive patterns. `examples/sample_workbook.xlsx` contains two worksheets to demonstrate the sheet-selection step. All personal-looking values are fictional and use `example.com`.
