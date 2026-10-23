"""Safe, bounded parsing for uploaded CSV and XLSX files.

The parser is intentionally independent from Django views. It never evaluates
cell content, formulas, or macros. Files are read with pandas/openpyxl in
read-only modes and all values are treated as untrusted data.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd
from openpyxl import load_workbook

SUPPORTED_FORMATS = {"csv", "xlsx"}
MISSING_TOKENS = {"", "na", "n/a", "null", "none", "nan", "nil"}
CSV_DELIMITERS = (",", ";", "\t", "|")
MAX_HEADER_CELLS = 2_000


class DatasetParseError(ValueError):
    """Raised when a file cannot be safely interpreted as tabular data."""


class SheetSelectionRequired(DatasetParseError):
    def __init__(self, sheet_names: list[str]):
        super().__init__("This workbook contains multiple sheets. Choose one.")
        self.sheet_names = sheet_names


@dataclass
class UploadInspection:
    file_format: str
    encoding: str | None = None
    delimiter: str | None = None
    delimiter_uncertain: bool = False
    sheet_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedDataset:
    frame: pd.DataFrame
    original_headers: list[str]
    file_format: str
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    sheet_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return int(len(self.frame.index))

    @property
    def column_count(self) -> int:
        return int(len(self.frame.columns))

    @property
    def parse_metadata(self) -> dict:
        return {
            "file_format": self.file_format,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "sheet_name": self.sheet_name,
            "sheet_names": self.sheet_names,
            "original_headers": self.original_headers,
            "warnings": self.warnings,
        }


def normalize_format(value: str) -> str:
    return value.lower().lstrip(".")


def sanitize_original_filename(filename: str) -> str:
    name = Path(filename or "dataset").name
    name = name.replace("\x00", "").strip()
    return name[:255] or "dataset"


def _decode_sample(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding, errors="strict")


def detect_text_encoding(path: Path) -> str:
    raw = path.read_bytes()[:131_072]
    if b"\x00" in raw:
        raise DatasetParseError(
            "This file appears to be binary. Upload a plain-text UTF-8 or Windows-1252 CSV."
        )
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            _decode_sample(raw, encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise DatasetParseError(
        "The CSV encoding is not supported. Save it as UTF-8 or Windows-1252 and try again."
    )


def _sample_lines(path: Path, encoding: str, max_lines: int = 25) -> list[str]:
    raw = path.read_bytes()[:262_144]
    text = _decode_sample(raw, encoding)
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[:max_lines]


def detect_csv_delimiter(path: Path, encoding: str) -> tuple[str | None, bool]:
    """Return (delimiter, uncertain).

    A delimiter is considered reliable when it produces more than one field on
    multiple lines and the field count is stable. If candidates tie, the caller
    is asked to choose.
    """
    lines = _sample_lines(path, encoding)
    if not lines:
        raise DatasetParseError("The CSV file is empty.")

    scores: dict[str, tuple[float, int]] = {}
    for delimiter in CSV_DELIMITERS:
        counts: list[int] = []
        for line in lines:
            try:
                row = next(csv.reader([line], delimiter=delimiter, strict=True))
            except csv.Error:
                continue
            counts.append(len(row))
        useful = [count for count in counts if count > 1]
        if not useful:
            continue
        consistency = sum(1 for count in counts if count == max(set(counts), key=counts.count))
        scores[delimiter] = (consistency / max(len(counts), 1), max(useful))

    if not scores:
        return None, True

    ranked = sorted(scores.items(), key=lambda item: (item[1][0], item[1][1]), reverse=True)
    best_delimiter, (best_consistency, best_columns) = ranked[0]
    if len(ranked) > 1:
        second_consistency, second_columns = ranked[1][1]
        if abs(best_consistency - second_consistency) < 0.01 and best_columns == second_columns:
            return None, True
    if best_consistency < 0.8:
        return None, True
    return best_delimiter, False


def _read_csv_header(path: Path, encoding: str, delimiter: str) -> list[str]:
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        try:
            row = next(reader)
        except StopIteration as exc:
            raise DatasetParseError("The CSV file is empty.") from exc
    headers = [str(value).strip() for value in row]
    if not any(headers):
        raise DatasetParseError("The CSV file does not contain a usable header row.")
    return headers[:MAX_HEADER_CELLS]


def _unique_headers(headers: Iterable[object]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for index, raw in enumerate(headers):
        name = str(raw).strip() if raw is not None else ""
        name = name.replace("\x00", "") or f"column_{index + 1}"
        name = name[:300]
        count = seen.get(name.casefold(), 0)
        seen[name.casefold()] = count + 1
        if count:
            suffix = 2
            candidate = f"{name}__{suffix}"
            while candidate.casefold() in seen:
                suffix += 1
                candidate = f"{name}__{suffix}"
            seen[candidate.casefold()] = 1
            name = candidate
        result.append(name)
    return result


def _clean_cell(value: object) -> object:
    if value is None:
        return pd.NA
    try:
        if pd.isna(value):
            return pd.NA
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.casefold() in MISSING_TOKENS:
            return pd.NA
        return value
    return value


def _normalize_frame(frame: pd.DataFrame, headers: list[str]) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = _unique_headers(headers)
    frame = frame.map(_clean_cell)
    frame = frame.reset_index(drop=True)
    return frame


def _validate_xlsx_container(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise DatasetParseError("This file is not a valid XLSX workbook.")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise DatasetParseError("This XLSX workbook is corrupt or unreadable.") from exc
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise DatasetParseError("This file is not a supported XLSX workbook.")


def _workbook_sheet_names(path: Path) -> list[str]:
    _validate_xlsx_container(path)
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - vendor exceptions vary
        raise DatasetParseError("This XLSX workbook is corrupt or unreadable.") from exc
    try:
        names = [sheet.title for sheet in workbook.worksheets]
    finally:
        workbook.close()
    if not names:
        raise DatasetParseError("This workbook does not contain any sheets.")
    return names


def inspect_upload(path: Path, file_format: str) -> UploadInspection:
    file_format = normalize_format(file_format)
    if file_format not in SUPPORTED_FORMATS:
        raise DatasetParseError("Unsupported file format. Upload a CSV or XLSX file.")
    if not path.exists() or path.stat().st_size == 0:
        raise DatasetParseError("The uploaded file is empty.")

    if file_format == "xlsx":
        sheets = _workbook_sheet_names(path)
        return UploadInspection(file_format="xlsx", sheet_names=sheets)

    encoding = detect_text_encoding(path)
    delimiter, uncertain = detect_csv_delimiter(path, encoding)
    warnings: list[str] = []
    if uncertain:
        warnings.append(
            "The CSV delimiter could not be determined reliably. Choose one manually."
        )
    return UploadInspection(
        file_format="csv",
        encoding=encoding,
        delimiter=delimiter,
        delimiter_uncertain=uncertain,
        warnings=warnings,
    )


def _read_csv(
    path: Path,
    *,
    delimiter: str | None,
    encoding: str | None,
) -> tuple[pd.DataFrame, list[str], str, str, list[str]]:
    resolved_encoding = encoding or detect_text_encoding(path)
    warnings: list[str] = []
    if delimiter is None:
        detected, uncertain = detect_csv_delimiter(path, resolved_encoding)
        if uncertain:
            raise DatasetParseError("Choose a CSV delimiter before importing this file.")
        delimiter = detected
    if delimiter not in CSV_DELIMITERS:
        raise DatasetParseError("Unsupported CSV delimiter.")

    headers = _read_csv_header(path, resolved_encoding, delimiter)
    if len(headers) > MAX_HEADER_CELLS:
        raise DatasetParseError(f"Datasets are limited to {MAX_HEADER_CELLS} columns.")

    try:
        frame = pd.read_csv(
            path,
            dtype=object,
            keep_default_na=False,
            na_filter=False,
            encoding=resolved_encoding,
            sep=delimiter,
            engine="python",
            on_bad_lines="error",
        )
    except pd.errors.EmptyDataError as exc:
        raise DatasetParseError("The CSV file has no header or data rows.") from exc
    except (pd.errors.ParserError, UnicodeError, ValueError, OSError) as exc:
        raise DatasetParseError(
            "The CSV file could not be parsed. Check its delimiter, encoding, and structure."
        ) from exc

    if frame.shape[1] != len(headers):
        warnings.append("The parsed column count differs from the raw header count.")
        if frame.shape[1] == 0:
            raise DatasetParseError("The CSV file does not contain usable columns.")
        if len(headers) > frame.shape[1]:
            headers = headers[: frame.shape[1]]
        else:
            headers = headers + [
                f"column_{index + 1}" for index in range(len(headers), frame.shape[1])
            ]
    if frame.empty:
        raise DatasetParseError("The dataset has a header but no data rows.")

    return _normalize_frame(frame, headers), headers, resolved_encoding, delimiter, warnings


def _read_xlsx(
    path: Path,
    *,
    sheet_name: str | None,
) -> tuple[pd.DataFrame, list[str], str, list[str]]:
    sheet_names = _workbook_sheet_names(path)
    selected_sheet = sheet_name or (sheet_names[0] if len(sheet_names) == 1 else None)
    if selected_sheet is None:
        raise SheetSelectionRequired(sheet_names)
    if selected_sheet not in sheet_names:
        raise DatasetParseError("The selected sheet does not exist in this workbook.")

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise DatasetParseError("This XLSX workbook is corrupt or unreadable.") from exc
    try:
        sheet = workbook[selected_sheet]
        first_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
    finally:
        workbook.close()
    headers = [str(value).strip() if value is not None else "" for value in (first_row or [])]
    if not any(headers):
        raise DatasetParseError("The selected sheet does not contain a usable header row.")
    headers = headers[:MAX_HEADER_CELLS]

    try:
        frame = pd.read_excel(
            path,
            sheet_name=selected_sheet,
            dtype=object,
            keep_default_na=False,
            engine="openpyxl",
        )
    except (ValueError, OSError, KeyError) as exc:
        raise DatasetParseError(
            "The XLSX workbook could not be parsed. Check the selected sheet."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - openpyxl/zlib vendor errors
        raise DatasetParseError("The XLSX workbook is corrupt or unreadable.") from exc

    if frame.empty:
        raise DatasetParseError("The selected sheet has a header but no data rows.")

    frame = _normalize_frame(frame, headers)
    return frame, headers, selected_sheet, []


def parse_dataset(
    path: Path,
    file_format: str,
    *,
    delimiter: str | None = None,
    encoding: str | None = None,
    sheet_name: str | None = None,
) -> ParsedDataset:
    file_format = normalize_format(file_format)
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise DatasetParseError("The uploaded file is empty.")

    if file_format == "csv":
        frame, headers, resolved_encoding, resolved_delimiter, warnings = _read_csv(
            path, delimiter=delimiter, encoding=encoding
        )
        return ParsedDataset(
            frame=frame,
            original_headers=headers,
            file_format="csv",
            encoding=resolved_encoding,
            delimiter=resolved_delimiter,
            warnings=warnings,
        )
    if file_format == "xlsx":
        frame, headers, selected_sheet, warnings = _read_xlsx(path, sheet_name=sheet_name)
        return ParsedDataset(
            frame=frame,
            original_headers=headers,
            file_format="xlsx",
            sheet_name=selected_sheet,
            warnings=warnings,
        )
    raise DatasetParseError("Unsupported file format. Upload a CSV or XLSX file.")


def read_preview(parsed: ParsedDataset, limit: int = 100) -> dict:
    safe_limit = max(1, min(int(limit), 100))
    view = parsed.frame.head(safe_limit)
    rows: list[list[str]] = []
    for _, row in view.iterrows():
        values: list[str] = []
        for value in row.tolist():
            if value is pd.NA or value is None:
                values.append("")
                continue
            try:
                if pd.isna(value):
                    values.append("")
                    continue
            except (TypeError, ValueError):
                pass
            text = str(value)
            if len(text) > 500:
                text = text[:497] + "…"
            values.append(text)
        rows.append(values)
    return {
        "columns": [str(column) for column in parsed.frame.columns],
        "rows": rows,
        "total_rows": parsed.row_count,
        "total_columns": parsed.column_count,
        "shown_rows": len(rows),
        "truncated": parsed.row_count > safe_limit,
        "limit": safe_limit,
    }
