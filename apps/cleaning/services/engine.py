"""Preview and apply orchestration for cleaning jobs."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.datasets.models import Dataset, DatasetVersion
from apps.datasets.services.privacy import (
    is_sensitive_column_name,
    redact_preview_value,
)
from apps.datasets.services.storage import (
    dataset_relative_path,
    delete_private_file,
    write_dataframe,
)
from apps.profiling.services.inference import is_missing
from apps.profiling.services.profiler import compute_dataset_metrics
from apps.profiling.services.version_io import load_dataset_version, parse_metadata

from .operations import (
    OPERATIONS,
    CleaningValidationError,
    OperationResult,
    apply_operation,
)

logger = logging.getLogger(__name__)

MAX_PREVIEW_EXAMPLES = 8


def _equal_value(before: Any, after: Any) -> bool:
    before_missing = is_missing(before)
    after_missing = is_missing(after)
    if before_missing or after_missing:
        return before_missing and after_missing
    try:
        return bool(before == after)
    except (TypeError, ValueError):
        return False


def _row_delta_operation(operation: str) -> bool:
    return operation in {"drop_empty_rows", "drop_duplicates"} or (
        operation == "handle_missing"
    )


def _safe_value(value: Any, *, operation: str, column: str, before: bool) -> str:
    if operation == "mask_identifier" and before:
        return "[redacted]"
    if is_sensitive_column_name(column) and before:
        return redact_preview_value(value, force_redact=True)
    return redact_preview_value(value)


def _warnings_for_operation(
    operation: str,
    config: dict,
    *,
    rows_before: int,
    rows_after: int,
    changed_cells: int,
    column_mapping: dict[str, str],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    dropped = rows_before - rows_after
    if dropped > 0:
        warnings.append(
            {
                "level": "warning",
                "text": (
                    f"{dropped:,} row(s) will be removed from the new version. "
                    "The source version remains unchanged."
                ),
            }
        )
    if rows_after == 0:
        warnings.append(
            {
                "level": "danger",
                "text": "This operation would remove every row and cannot be applied.",
            }
        )
    if operation == "handle_missing" and config.get("strategy") in {"mean", "median"}:
        warnings.append(
            {
                "level": "warning",
                "text": (
                    "Mean/median filling changes the distribution and can understate "
                    "uncertainty. Document the choice in your analysis."
                ),
            }
        )
    if operation == "handle_missing" and config.get("strategy") == "constant":
        warnings.append(
            {
                "level": "warning",
                "text": "A constant fill value introduces information that was not in the source.",
            }
        )
    if operation == "normalize_categories":
        warnings.append(
            {
                "level": "warning",
                "text": (
                    "Category mapping can merge values with different meanings. "
                    "Only exact matches are replaced."
                ),
            }
        )
    if operation == "normalize_column_names" and column_mapping:
        warnings.append(
            {
                "level": "info",
                "text": "Column names change in this version. Update downstream references.",
            }
        )
    if operation in {"mask_email", "mask_phone", "mask_identifier"}:
        warnings.append(
            {
                "level": "warning",
                "text": (
                    "Masking and pseudonymization are not formal anonymization guarantees. "
                    "Masked results may still be identifiable in combination with other data."
                ),
            }
        )
    if operation == "mask_identifier":
        warnings.append(
            {
                "level": "info",
                "text": (
                    "A per-job secret salt is used. Identical source values receive the same "
                    "pseudonym within this dataset version."
                ),
            }
        )
    if operation == "convert_type":
        warnings.append(
            {
                "level": "info",
                "text": (
                    "Conversion is rejected if any non-missing value is invalid. "
                    "The displayed counts reflect the current source version."
                ),
            }
        )
    if changed_cells == 0 and not column_mapping:
        warnings.append(
            {
                "level": "info",
                "text": "No values will change for this configuration.",
            }
        )
    return warnings


def build_cleaning_preview(
    version: DatasetVersion,
    config: dict[str, Any],
) -> dict[str, Any]:
    operation = str(config.get("operation", ""))
    definition = OPERATIONS.get(operation)
    if definition is None:
        raise CleaningValidationError("Choose a supported cleaning operation.")

    parsed = load_dataset_version(version)
    before_frame = parsed.frame
    result: OperationResult = apply_operation(before_frame, config)
    after_frame = result.frame

    before_metrics = compute_dataset_metrics(before_frame)
    after_metrics = compute_dataset_metrics(after_frame)

    changed_cells = 0
    affected_rows = 0
    examples: list[dict[str, Any]] = []
    examples_truncated = False

    same_shape = before_frame.shape == after_frame.shape
    if same_shape:
        affected_positions: set[int] = set()
        for column_index, before_column in enumerate(before_frame.columns):
            after_column = after_frame.columns[column_index]
            for position, (before_value, after_value) in enumerate(
                zip(before_frame.iloc[:, column_index].tolist(), after_frame.iloc[:, column_index].tolist())
            ):
                if not _equal_value(before_value, after_value):
                    changed_cells += 1
                    affected_positions.add(position)
                    if len(examples) < MAX_PREVIEW_EXAMPLES:
                        examples.append(
                            {
                                "row": position + 1,
                                "column": str(after_column),
                                "before": _safe_value(
                                    before_value,
                                    operation=operation,
                                    column=str(before_column),
                                    before=True,
                                ),
                                "after": _safe_value(
                                    after_value,
                                    operation=operation,
                                    column=str(after_column),
                                    before=False,
                                ),
                            }
                        )
                    else:
                        examples_truncated = True
        affected_rows = len(affected_positions)
    else:
        affected_rows = max(0, before_metrics.row_count - after_metrics.row_count)

    column_mapping_rows = [
        {"from": source, "to": target}
        for source, target in result.column_mapping.items()
    ]
    if len(column_mapping_rows) > 30:
        column_mapping_rows = column_mapping_rows[:30]

    safe_config = {
        key: value
        for key, value in config.items()
        if key not in {"salt", "mapping"}
    }
    if isinstance(config.get("mapping"), dict):
        safe_config["mapping_count"] = len(config["mapping"])

    warnings = _warnings_for_operation(
        operation,
        config,
        rows_before=before_metrics.row_count,
        rows_after=after_metrics.row_count,
        changed_cells=changed_cells,
        column_mapping=result.column_mapping,
    )

    return {
        "operation": operation,
        "operation_label": definition.label,
        "operation_description": definition.description,
        "note": definition.note,
        "sensitive": definition.sensitive,
        "columns": [str(column) for column in (config.get("columns") or [])],
        "safe_config": safe_config,
        "rows_before": before_metrics.row_count,
        "rows_after": after_metrics.row_count,
        "columns_before": before_metrics.column_count,
        "columns_after": after_metrics.column_count,
        "changed_cells": changed_cells,
        "affected_rows": affected_rows,
        "dropped_rows": max(0, before_metrics.row_count - after_metrics.row_count),
        "examples": examples,
        "examples_truncated": examples_truncated,
        "column_mapping": column_mapping_rows,
        "column_mapping_truncated": len(result.column_mapping) > len(column_mapping_rows),
        "warnings": warnings,
        "can_apply": after_metrics.row_count > 0 and after_metrics.column_count > 0,
        "metrics": {
            "before": before_metrics.as_dict(),
            "after": after_metrics.as_dict(),
        },
        "source_version": version.version_number,
        "row_delta_operation": _row_delta_operation(operation),
    }


def _output_parse_metadata(
    source_version: DatasetVersion,
    after_frame: pd.DataFrame,
    *,
    sheet_name: str | None = None,
) -> dict[str, Any]:
    source_meta = parse_metadata(source_version)
    dataset = source_version.dataset
    if dataset.file_format == "xlsx":
        resolved_sheet = sheet_name or source_meta.get("sheet_name") or "Cleaned Data"
        return {
            "file_format": "xlsx",
            "sheet_name": resolved_sheet,
            "sheet_names": [resolved_sheet],
            "encoding": None,
            "delimiter": None,
            "original_headers": [str(column) for column in after_frame.columns],
            "warnings": [],
        }
    delimiter = source_meta.get("delimiter")
    if delimiter not in {",", ";", "\t", "|"}:
        delimiter = ","
    return {
        "file_format": "csv",
        "encoding": "utf-8-sig",
        "delimiter": delimiter,
        "sheet_name": None,
        "sheet_names": [],
        "original_headers": [str(column) for column in after_frame.columns],
        "warnings": [],
    }


def apply_cleaning_job(job, *, owner) -> tuple[DatasetVersion, int]:
    """Apply a previewed job and create a new immutable version.

    Returns (output_version, analysis_run_id). The source file is never modified.
    """
    from apps.profiling.services.analysis import analyze_version

    if job.dataset.owner_id != owner.id:
        raise CleaningValidationError("You do not have access to this cleaning job.")
    if job.status != job.Status.PREVIEWED:
        raise CleaningValidationError("This cleaning job is not awaiting confirmation.")
    if job.output_version_id:
        raise CleaningValidationError("This cleaning job has already produced a version.")

    source_version = job.source_version
    if source_version.dataset_id != job.dataset_id:
        raise CleaningValidationError("The source version does not belong to this dataset.")

    parsed = load_dataset_version(source_version)
    result = apply_operation(parsed.frame, job.operation_config)
    after_frame = result.frame
    if after_frame.shape[0] == 0:
        raise CleaningValidationError(
            "This operation would remove every row. Adjust the configuration and preview again."
        )
    if after_frame.shape[1] == 0:
        raise CleaningValidationError("The operation would leave no columns.")

    dataset = job.dataset
    source_meta = parse_metadata(source_version)
    output_meta = _output_parse_metadata(
        source_version,
        after_frame,
        sheet_name=source_meta.get("sheet_name"),
    )
    delimiter = output_meta.get("delimiter") or ","
    relative_path = dataset_relative_path(dataset.owner_id, dataset.file_format)
    try:
        write_dataframe(
            after_frame,
            relative_path,
            dataset.file_format,
            delimiter=delimiter,
            sheet_name=output_meta.get("sheet_name") or "Cleaned Data",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cleaning output write failed type=%s", exc.__class__.__name__)
        raise CleaningValidationError(
            "The cleaned file could not be written safely. The source version was not changed."
        ) from exc

    committed = False
    try:
        with transaction.atomic():
            locked_dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
            locked_job = type(job).objects.select_for_update().get(pk=job.pk)
            if locked_job.status != locked_job.Status.PREVIEWED:
                raise CleaningValidationError("This cleaning job has already been processed.")
            if locked_job.output_version_id:
                raise CleaningValidationError("This cleaning job already has an output version.")
            if locked_job.source_version.dataset_id != locked_dataset.pk:
                raise CleaningValidationError("The job source does not belong to this dataset.")

            next_number = (
                locked_dataset.versions.aggregate(maximum=Max("version_number"))["maximum"] or 0
            ) + 1
            processing_summary = {
                "parse": output_meta,
                "cleaning": {
                    "job_id": locked_job.pk,
                    "operation": locked_job.operation_config.get("operation"),
                    "operation_label": OPERATIONS.get(
                        str(locked_job.operation_config.get("operation")), None
                    ).label
                    if OPERATIONS.get(str(locked_job.operation_config.get("operation")))
                    else str(locked_job.operation_config.get("operation")),
                    "source_version": source_version.version_number,
                    "changed_cells": (locked_job.preview_summary or {}).get("changed_cells"),
                    "dropped_rows": (locked_job.preview_summary or {}).get("dropped_rows"),
                },
            }
            output_version = DatasetVersion.objects.create(
                dataset=locked_dataset,
                version_number=next_number,
                file_path=relative_path,
                version_type=DatasetVersion.Type.CLEANED,
                row_count=int(after_frame.shape[0]),
                column_count=int(after_frame.shape[1]),
                parent_version=source_version,
                processing_summary=processing_summary,
            )
            locked_job.output_version = output_version
            locked_job.status = locked_job.Status.APPLIED
            locked_job.completed_at = timezone.now()
            preview_summary = dict(locked_job.preview_summary or {})
            preview_summary["applied"] = True
            preview_summary["output_version"] = next_number
            locked_job.preview_summary = preview_summary
            locked_job.save(
                update_fields=["output_version", "status", "completed_at", "preview_summary"]
            )
            Dataset.objects.filter(pk=locked_dataset.pk).update(
                row_count=int(after_frame.shape[0]),
                column_count=int(after_frame.shape[1]),
                status=Dataset.Status.ANALYZING,
            )
            AuditEvent.objects.create(
                owner=owner,
                dataset=locked_dataset,
                action="cleaning.applied",
                safe_metadata={
                    "job_id": locked_job.pk,
                    "operation": locked_job.operation_config.get("operation"),
                    "source_version": source_version.version_number,
                    "output_version": next_number,
                    "changed_cells": (locked_job.preview_summary or {}).get("changed_cells"),
                },
            )
        committed = True
    except Exception:
        if not committed:
            delete_private_file(relative_path)
        raise

    analysis_run = analyze_version(output_version)
    return output_version, analysis_run.pk
