"""Import a temporary upload into private storage and the database."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from apps.audit.models import AuditEvent
from apps.datasets.models import Dataset, DatasetVersion
from apps.profiling.models import AnalysisRun
from apps.profiling.services.analysis import analyze_version
from apps.profiling.services.parser import parse_dataset

from .storage import delete_private_file, promote_temp_upload

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    dataset: Dataset
    version: DatasetVersion
    analysis_run: AnalysisRun


class ImportError(ValueError):
    pass


def import_temp_dataset(
    *,
    temp_relative_path: str,
    original_filename: str,
    file_format: str,
    project,
    owner,
    delimiter: str | None = None,
    encoding: str | None = None,
    sheet_name: str | None = None,
) -> ImportResult:
    if project.owner_id != owner.id:
        raise ImportError("You do not have access to this project.")

    temp_path = None
    from .storage import resolve_private_path

    try:
        temp_path = resolve_private_path(temp_relative_path)
    except Exception as exc:  # noqa: BLE001
        raise ImportError("The temporary upload could not be found.") from exc
    if not temp_path.exists():
        raise ImportError("The temporary upload has expired. Upload the file again.")

    parsed = parse_dataset(
        temp_path,
        file_format,
        delimiter=delimiter,
        encoding=encoding,
        sheet_name=sheet_name,
    )

    final_relative, final_size = promote_temp_upload(
        temp_relative_path, owner.id, file_format
    )
    try:
        with transaction.atomic():
            dataset = Dataset.objects.create(
                project=project,
                owner=owner,
                original_filename=original_filename[:255],
                stored_file_path=final_relative,
                file_format=file_format,
                file_size=final_size,
                row_count=parsed.row_count,
                column_count=parsed.column_count,
                status=Dataset.Status.ANALYZING,
            )
            version = DatasetVersion.objects.create(
                dataset=dataset,
                version_number=1,
                file_path=final_relative,
                version_type=DatasetVersion.Type.ORIGINAL,
                row_count=parsed.row_count,
                column_count=parsed.column_count,
                processing_summary={"parse": parsed.parse_metadata},
            )
            AuditEvent.objects.create(
                owner=owner,
                dataset=dataset,
                action="dataset.uploaded",
                safe_metadata={
                    "file_format": file_format,
                    "file_size": final_size,
                    "row_count": parsed.row_count,
                    "column_count": parsed.column_count,
                    "version_number": 1,
                },
            )
    except Exception:
        delete_private_file(final_relative)
        raise

    analysis_run = analyze_version(version)
    return ImportResult(dataset=dataset, version=version, analysis_run=analysis_run)
