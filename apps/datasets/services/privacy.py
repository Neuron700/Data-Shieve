"""Reversible/tokenizing masking helpers and preview redaction."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pandas as pd

EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
PHONE_CANDIDATE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().\-]{6,}\d)(?!\d)")
NIK_RE = re.compile(r"(?<!\d)\d{16}(?!\d)")
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
SENSITIVE_NAME_RE = re.compile(
    r"(?:e-?mail|phone|telephone|mobile|whatsapp|nik|ktp|passport|ssn|"
    r"credit.?card|card.?number|account.?number|iban|address|alamat|"
    r"full.?name|first.?name|last.?name|birth|dob|tanggal.?lahir|npwp)",
    re.IGNORECASE,
)


def mask_email_text(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        local, domain = match.group(1), match.group(2)
        visible = local[:1] if local else ""
        return f"{visible}***@{domain}"

    return EMAIL_RE.sub(replace, value)


def _mask_phone_candidate(candidate: str) -> str:
    digits = re.sub(r"\D", "", candidate)
    if not 9 <= len(digits) <= 15:
        return candidate
    prefix = "+" if candidate.strip().startswith("+") else ""
    return prefix + ("*" * max(0, len(digits) - 4)) + digits[-4:]


def mask_phone_text(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(1)
        digits = re.sub(r"\D", "", candidate)
        if not 9 <= len(digits) <= 15:
            return candidate
        if not (candidate.strip().startswith("+") or digits.startswith("0") or len(digits) >= 10):
            return candidate
        return _mask_phone_candidate(candidate)

    return PHONE_CANDIDATE_RE.sub(replace, value)


def pseudonymize(value: Any, salt: str, prefix: str = "ID-") -> str:
    payload = f"{salt}:{value}".encode("utf-8", errors="replace")
    digest = hashlib.sha256(payload).hexdigest()[:12]
    return f"{prefix}{digest}"


def is_sensitive_column_name(column_name: str) -> bool:
    return bool(SENSITIVE_NAME_RE.search(column_name))


def redact_preview_value(value: Any, *, force_redact: bool = False) -> str:
    if value is None or value is pd.NA:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value)
    if force_redact:
        return "[redacted]"
    text = mask_email_text(text)
    text = NIK_RE.sub(lambda match: "*" * 12 + match.group(0)[-4:], text)
    text = LONG_NUMBER_RE.sub(lambda match: "*" * 12 + match.group(0)[-4:], text)
    text = mask_phone_text(text)
    if len(text) > 120:
        return text[:117] + "…"
    return text


def preview_masks_sensitive_column(column_name: str) -> bool:
    return is_sensitive_column_name(column_name)


def redact_preview(preview: dict[str, Any]) -> dict[str, Any]:
    columns = [str(column) for column in preview.get("columns", [])]
    rows = preview.get("rows", [])
    sensitive_columns = [
        column for column in columns if is_sensitive_column_name(column)
    ]
    redacted_rows: list[list[str]] = []
    for row in rows:
        values: list[str] = []
        for index, value in enumerate(row):
            column = columns[index] if index < len(columns) else ""
            values.append(
                redact_preview_value(
                    value,
                    force_redact=is_sensitive_column_name(column),
                )
            )
        redacted_rows.append(values)
    redacted = dict(preview)
    redacted["rows"] = redacted_rows
    redacted["masked_columns"] = sensitive_columns
    redacted["redaction_applied"] = bool(sensitive_columns)
    return redacted
