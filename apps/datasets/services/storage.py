"""Private file storage helpers.

All relative paths are generated server-side. User-supplied file names never
become storage paths. Paths supplied by callers are resolved beneath the
configured private root before any filesystem operation.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

import pandas as pd
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter

from apps.profiling.services.parser import normalize_format


class StorageError(ValueError):
    pass


def private_root() -> Path:
    root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_storage_dirs() -> None:
    Path(settings.TEMP_UPLOAD_ROOT).mkdir(parents=True, exist_ok=True)
    Path(settings.DATASET_STORAGE_ROOT).mkdir(parents=True, exist_ok=True)


def resolve_private_path(relative_path: str) -> Path:
    root = private_root()
    candidate = (root / relative_path).resolve()
    try:
        if os.path.commonpath([str(root), str(candidate)]) != str(root):
            raise StorageError("Invalid private storage path.")
    except ValueError as exc:
        raise StorageError("Invalid private storage path.") from exc
    return candidate


def _format_extension(file_format: str) -> str:
    normalized = normalize_format(file_format)
    if normalized not in {"csv", "xlsx"}:
        raise StorageError("Unsupported storage format.")
    return normalized


def dataset_relative_path(owner_id: int, file_format: str) -> str:
    extension = _format_extension(file_format)
    return f"datasets/{int(owner_id)}/{uuid.uuid4().hex}.{extension}"


def temp_relative_path(owner_id: int, file_format: str) -> str:
    extension = _format_extension(file_format)
    return f"tmp/{int(owner_id)}/{uuid.uuid4().hex}.{extension}"


def _write_chunks(source: IO[bytes], destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    total = 0
    try:
        with temp.open("xb") as handle:
            for chunk in source.chunks():
                if not chunk:
                    continue
                total += len(chunk)
                if total > settings.MAX_UPLOAD_SIZE:
                    raise StorageError(
                        f"The file exceeds the {settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB limit."
                    )
                handle.write(chunk)
        if total == 0:
            raise StorageError("The uploaded file is empty.")
        os.replace(temp, destination)
    except Exception:
        temp.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    return total


def save_temp_upload(upload: UploadedFile, owner_id: int, file_format: str) -> tuple[str, int]:
    ensure_storage_dirs()
    relative = temp_relative_path(owner_id, file_format)
    path = resolve_private_path(relative)
    size = _write_chunks(upload, path)
    return relative, size


def save_uploaded_file(upload: UploadedFile, owner_id: int, file_format: str) -> tuple[str, int]:
    ensure_storage_dirs()
    relative = dataset_relative_path(owner_id, file_format)
    path = resolve_private_path(relative)
    size = _write_chunks(upload, path)
    return relative, size


def promote_temp_upload(temp_relative: str, owner_id: int, file_format: str) -> tuple[str, int]:
    ensure_storage_dirs()
    source = resolve_private_path(temp_relative)
    if not source.exists():
        raise StorageError("The temporary upload has expired. Upload the file again.")
    relative = dataset_relative_path(owner_id, file_format)
    destination = resolve_private_path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    return relative, destination.stat().st_size


def copy_file_to_dataset(source_relative: str, owner_id: int, file_format: str) -> tuple[str, int]:
    ensure_storage_dirs()
    source = resolve_private_path(source_relative)
    if not source.exists():
        raise StorageError("The source version file is missing.")
    relative = dataset_relative_path(owner_id, file_format)
    destination = resolve_private_path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return relative, destination.stat().st_size


def delete_private_file(relative_path: str | None) -> None:
    if not relative_path:
        return
    try:
        path = resolve_private_path(relative_path)
        path.unlink(missing_ok=True)
        parent = path.parent
        root = private_root()
        if parent != root and root in parent.parents and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except (StorageError, OSError):
        return


def delete_temp_upload(relative_path: str | None) -> None:
    delete_private_file(relative_path)


def cleanup_stale_temp_uploads(max_age_seconds: int | None = None) -> int:
    root = Path(settings.TEMP_UPLOAD_ROOT)
    if not root.exists():
        return 0
    max_age = max_age_seconds or int(settings.TEMP_UPLOAD_MAX_AGE_SECONDS)
    cutoff = time.time() - max_age
    removed = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    for directory in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            continue
    return removed


def _safe_excel_string(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _write_excel_cell(cell: Cell, value: Any) -> None:
    safe = _safe_excel_string(value)
    cell.value = safe
    if isinstance(safe, str) and safe[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        cell.data_type = "s"


def write_dataframe(
    frame: pd.DataFrame,
    relative_path: str,
    file_format: str,
    *,
    delimiter: str = ",",
    sheet_name: str = "Data",
) -> None:
    ensure_storage_dirs()
    destination = resolve_private_path(relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    try:
        if file_format == "csv":
            frame.to_csv(
                temp,
                index=False,
                encoding="utf-8-sig",
                sep=delimiter,
                na_rep="",
                lineterminator="\n",
            )
        elif file_format == "xlsx":
            workbook = Workbook()
            sheet = workbook.active
            safe_title = "".join(
                character for character in (sheet_name or "Data") if character not in r"[]:*?/\\'"
            ).strip()[:31] or "Data"
            sheet.title = safe_title
            for column_index, column_name in enumerate(frame.columns, start=1):
                cell = sheet.cell(row=1, column=column_index, value=str(column_name))
                cell.data_type = "s"
            for row_index, row in enumerate(frame.itertuples(index=False, name=None), start=2):
                for column_index, value in enumerate(row, start=1):
                    _write_excel_cell(
                        sheet.cell(row=row_index, column=column_index),
                        value,
                    )
            if len(frame.columns):
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = (
                    f"A1:{get_column_letter(len(frame.columns))}{max(len(frame.index) + 1, 1)}"
                )
            workbook.save(temp)
        else:
            raise StorageError("Unsupported output format.")
        os.replace(temp, destination)
    except Exception:
        temp.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


def write_bytes_atomic(relative_path: str, content: bytes) -> Path:
    destination = resolve_private_path(relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    temp.write_bytes(content)
    os.replace(temp, destination)
    return destination
