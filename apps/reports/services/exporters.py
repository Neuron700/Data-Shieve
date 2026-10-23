"""Report and download generation.

Spreadsheet exports are treated as untrusted text sinks. Values beginning with
formula characters are prefixed in CSV and forced to string cells in XLSX.
The stored dataset versions themselves are never modified.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime
from typing import Any

import pandas as pd
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter

from apps.cleaning.models import CleaningJob
from apps.profiling.models import AnalysisRun, ColumnProfile
from apps.quality.models import QualityIssue

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _display_value(value: Any) -> Any:
    if _missing(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def sanitize_csv_value(value: Any) -> Any:
    displayed = _display_value(value)
    if isinstance(displayed, str):
        if displayed.lstrip()[:1] in {"=", "+", "-", "@", "\t", "\r"}:
            return "'" + displayed
    return displayed


def to_csv_bytes(frame: pd.DataFrame, delimiter: str = ",") -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow([sanitize_csv_value(column) for column in frame.columns])
    for row in frame.itertuples(index=False, name=None):
        writer.writerow([sanitize_csv_value(value) for value in row])
    return buffer.getvalue().encode("utf-8-sig")


def _write_excel_cell(cell: Cell, value: Any) -> None:
    displayed = _display_value(value)
    cell.value = displayed
    if isinstance(displayed, str) and displayed.lstrip()[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        cell.data_type = "s"


def to_xlsx_bytes(frame: pd.DataFrame, sheet_name: str = "Export") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    title = re.sub(r"[\[\]:*?/\\']", "", sheet_name or "Export").strip()[:31] or "Export"
    sheet.title = title
    for column_index, column_name in enumerate(frame.columns, start=1):
        _write_excel_cell(sheet.cell(row=1, column=column_index), str(column_name))
    for row_index, row in enumerate(frame.itertuples(index=False, name=None), start=2):
        for column_index, value in enumerate(row, start=1):
            _write_excel_cell(sheet.cell(row=row_index, column=column_index), value)
    if len(frame.columns):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(frame.columns))}{max(len(frame.index) + 1, 1)}"
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _issue_payload(issue: QualityIssue) -> dict:
    return {
        "id": issue.pk,
        "rule_id": issue.rule_id,
        "severity": issue.severity,
        "issue_type": issue.issue_type,
        "column_name": issue.column_name,
        "issue_count": issue.issue_count,
        "explanation": issue.explanation,
        "recommendation": issue.recommendation,
        "sample_positions": issue.sample_positions,
        "status": issue.status,
    }


def _column_payload(column: ColumnProfile) -> dict:
    return {
        "column_name": column.column_name,
        "detected_type": column.detected_type,
        "non_null_count": column.non_null_count,
        "null_count": column.null_count,
        "unique_count": column.unique_count,
        "duplicate_value_count": column.duplicate_value_count,
        "summary_statistics": column.summary_statistics,
        "inferred_patterns": column.inferred_patterns,
        "quality_score": column.quality_score,
    }


def build_profiling_report(
    analysis_run: AnalysisRun,
    *,
    project_name: str,
    dataset_original_filename: str,
) -> dict:
    version = analysis_run.dataset_version
    dataset = version.dataset
    summary = analysis_run.summary or {}
    return {
        "report_type": "datasieve_profiling_report",
        "report_version": "1.0",
        "generated_at": timezone.now().isoformat(),
        "product": {
            "name": "DataSieve",
            "engine_version": summary.get("engine_version"),
            "formula_version": summary.get("formula_version"),
            "rules": summary.get("ruleset", []),
        },
        "project": {"name": project_name},
        "dataset": {
            "original_filename": dataset_original_filename,
            "file_format": dataset.file_format,
            "file_size": dataset.file_size,
        },
        "version": {
            "version_number": version.version_number,
            "version_type": version.version_type,
            "row_count": version.row_count,
            "column_count": version.column_count,
            "created_at": version.created_at.isoformat(),
        },
        "analysis": {
            "id": analysis_run.pk,
            "status": analysis_run.status,
            "started_at": analysis_run.started_at.isoformat(),
            "completed_at": analysis_run.completed_at.isoformat()
            if analysis_run.completed_at
            else None,
            "dataset_metrics": summary.get("dataset_metrics", {}),
            "score": summary.get("score", {}),
            "rule_summary": summary.get("rule_summary", []),
            "limitations": (summary.get("score") or {}).get("limitations", []),
        },
        "columns": [_column_payload(column) for column in analysis_run.column_profiles.all()],
        "quality_issues": [
            _issue_payload(issue) for issue in analysis_run.quality_issues.all()
        ],
    }


def build_quality_report(analysis_run: AnalysisRun) -> dict:
    summary = analysis_run.summary or {}
    version = analysis_run.dataset_version
    return {
        "report_type": "datasieve_quality_report",
        "report_version": "1.0",
        "generated_at": timezone.now().isoformat(),
        "analysis_id": analysis_run.pk,
        "dataset_version": version.version_number,
        "version_type": version.version_type,
        "score": summary.get("score", {}),
        "dataset_metrics": summary.get("dataset_metrics", {}),
        "rule_summary": summary.get("rule_summary", []),
        "issues": [
            _issue_payload(issue) for issue in analysis_run.quality_issues.all()
        ],
    }


def quality_csv_bytes(analysis_run: AnalysisRun) -> bytes:
    rows = []
    for issue in analysis_run.quality_issues.all():
        rows.append(
            {
                "rule_id": issue.rule_id,
                "severity": issue.severity,
                "issue_type": issue.issue_type,
                "column_name": issue.column_name,
                "issue_count": issue.issue_count,
                "status": issue.status,
                "explanation": issue.explanation,
                "recommendation": issue.recommendation,
                "sample_positions": " ".join(str(value) for value in issue.sample_positions),
            }
        )
    frame = pd.DataFrame(
        rows,
        columns=[
            "rule_id",
            "severity",
            "issue_type",
            "column_name",
            "issue_count",
            "status",
            "explanation",
            "recommendation",
            "sample_positions",
        ],
    )
    return to_csv_bytes(frame)


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def _md(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def build_cleaning_markdown(dataset) -> str:
    versions = list(
        dataset.versions.select_related("parent_version").order_by("version_number")
    )
    jobs = list(
        CleaningJob.objects.filter(dataset=dataset)
        .select_related("source_version", "output_version")
        .order_by("created_at")
    )
    version_ids = [version.pk for version in versions]
    runs = (
        AnalysisRun.objects.filter(
            dataset_version_id__in=version_ids, status="completed"
        )
        .order_by("started_at", "-pk")
    )
    run_map: dict[int, AnalysisRun] = {}
    for run in runs:
        run_map[run.dataset_version_id] = run

    lines: list[str] = [
        "# DataSieve cleaning summary",
        "",
        f"**Project:** {_md(dataset.project.name)}",
        f"**Dataset:** {_md(dataset.original_filename)}",
        f"**Format:** {dataset.file_format.upper()}",
        f"**Generated:** {timezone.now().isoformat()}",
        "",
        "Generated by DataSieve. The source version is preserved; every cleaning operation creates a new version.",
        "",
        "## Versions",
        "",
        "| Version | Type | Rows | Columns | Parent | Quality score | Missing cells | Duplicate rows |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for version in versions:
        run = run_map.get(version.pk)
        summary = (run.summary or {}) if run else {}
        metrics = summary.get("dataset_metrics", {})
        score = summary.get("score", {}).get("score")
        lines.append(
            "| v{number} | {type} | {rows} | {columns} | {parent} | {score} | {missing} | {duplicates} |".format(
                number=version.version_number,
                type=_md(version.get_version_type_display()),
                rows=version.row_count,
                columns=version.column_count,
                parent=f"v{version.parent_version.version_number}"
                if version.parent_version
                else "—",
                score=score if score is not None else "n/a",
                missing=metrics.get("missing_cells", "n/a"),
                duplicates=metrics.get("duplicate_rows", "n/a"),
            )
        )

    lines.extend(
        [
            "",
            "## Cleaning operations",
            "",
            "| Job | Operation | Source | Output | Status | Changed cells | Rows dropped |",
            "|---:|---|---|---|---|---:|---:|",
        ]
    )
    for job in jobs:
        preview = job.preview_summary or {}
        lines.append(
            "| {job} | {operation} | v{source} | {output} | {status} | {changed} | {dropped} |".format(
                job=job.pk,
                operation=_md(job.operation_config.get("operation", "unknown")),
                source=job.source_version.version_number,
                output=f"v{job.output_version.version_number}"
                if job.output_version
                else "—",
                status=_md(job.get_status_display()),
                changed=preview.get("changed_cells", "n/a"),
                dropped=preview.get("dropped_rows", "n/a"),
            )
        )

    lines.extend(
        [
            "",
            "## Notes and limitations",
            "",
            "- Quality scores follow DataSieve's documented product formula and are not an industry certification.",
            "- Potential outliers are flagged for review and do not reduce the score.",
            "- Masking and pseudonymization are not formal anonymization guarantees.",
            "- Sensitive-data detection is heuristic and may miss identifiers.",
            "- Review every cleaned version before using it for decisions or publication.",
            "",
        ]
    )
    return "\n".join(lines)
