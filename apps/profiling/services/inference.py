"""Deterministic type and pattern inference for tabular columns."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

BOOLEAN_TOKENS = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
}
INTEGER_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$")
SCIENTIFIC_RE = re.compile(r"^[+-]?\d+(?:[eE][+-]?\d+)$")
GROUPED_NUMBER_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
LEADING_ZERO_RE = re.compile(r"^[+-]?0\d+$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(
    r"^(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{1,4}\)?[\s.\-]?)?\d{3,4}[\s.\-]?\d{3,4}$"
)
NIK_RE = re.compile(r"^\d{16}$")

DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "YYYY-MM-DD"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "YYYY/MM/DD"),
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"), "DAY/MONTH/YEAR"),
    (re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"), "DAY-MONTH-YEAR"),
    (re.compile(r"^\d{1,2} [A-Za-z]{3,9} \d{4}$"), "Day Month Year"),
    (re.compile(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}$"), "Month Day, Year"),
    (re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?$"), "ISO datetime"),
    (re.compile(r"^\d{1,2}/\d{1,2}/\d{4} \d{2}:\d{2}(?::\d{2})?$"), "Day/Month/Year time"),
)

DATE_FORMAT_MAP: dict[str, str] = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "YYYY/MM/DD": "%Y/%m/%d",
    "DAY/MONTH/YEAR": None,  # handled separately because of ambiguity
    "DAY-MONTH-YEAR": None,
    "Day Month Year": "%d %b %Y",
    "Month Day, Year": "%b %d, %Y",
    "ISO datetime": "%Y-%m-%dT%H:%M:%S",
    "Day/Month/Year time": None,
}

TYPE_LABELS = {
    "integer": "Integer",
    "float": "Float",
    "boolean": "Boolean",
    "date": "Date",
    "datetime": "Datetime",
    "string": "Text",
    "mixed": "Mixed",
    "empty": "Empty",
}


@dataclass
class TypeInference:
    detected_type: str
    primary_kind: str
    kind_counts: dict[str, int] = field(default_factory=dict)
    non_null_count: int = 0
    matching_count: int = 0
    mismatch_count: int = 0
    date_formats: dict[str, int] = field(default_factory=dict)
    leading_zero_count: int = 0
    high_cardinality: bool = False
    possible_identifier: bool = False
    email_like_count: int = 0
    phone_like_count: int = 0
    nik_like_count: int = 0
    whitespace_count: int = 0

    @property
    def type_label(self) -> str:
        return TYPE_LABELS.get(self.detected_type, self.detected_type.title())


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(result, bool):
        return result
    return False


def parse_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return BOOLEAN_TOKENS.get(value.strip().casefold())
    return None


def parse_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        if pd.isna(value):
            return None
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if GROUPED_NUMBER_RE.fullmatch(text):
            text = text.replace(",", "")
        if INTEGER_RE.fullmatch(text) or FLOAT_RE.fullmatch(text) or SCIENTIFIC_RE.fullmatch(text):
            try:
                return float(text)
            except ValueError:
                return None
    return None


def parse_integer(value: Any) -> int | None:
    number = parse_float(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _label_for_ambiguous_date(first: int, second: int) -> tuple[str, bool]:
    if first > 12 and second <= 12:
        return "DD/MM/YYYY", False
    if second > 12 and first <= 12:
        return "MM/DD/YYYY", False
    return "DD/MM/YYYY (ambiguous)", True


def parse_date_value(value: Any) -> tuple[date | datetime, str] | None:
    """Parse a known date format. Numeric identifiers are never treated as dates."""
    if is_missing(value):
        return None
    if isinstance(value, datetime):
        return value, "Excel datetime" if not isinstance(value, date) else "Excel date"
    if isinstance(value, date):
        return value, "Excel date"
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    for pattern, label in DATE_PATTERNS:
        if not pattern.fullmatch(text):
            continue
        try:
            if label == "DAY/MONTH/YEAR":
                first, second, year = (int(part) for part in text.split("/"))
                actual_label, _ambiguous = _label_for_ambiguous_date(first, second)
                return datetime(year, second, first).date(), actual_label
            if label == "DAY-MONTH-YEAR":
                first, second, year = (int(part) for part in text.split("-"))
                if first > 12 and second <= 12:
                    return datetime(year, second, first).date(), "DD-MM-YYYY"
                if second > 12 and first <= 12:
                    return datetime(year, first, second).date(), "MM-DD-YYYY"
                return datetime(year, second, first).date(), "DD-MM-YYYY (ambiguous)"
            if label == "Day/Month/Year time":
                date_part, time_part = text.split(" ", 1)
                first, second, year = (int(part) for part in date_part.split("/"))
                actual_label, _ambiguous = _label_for_ambiguous_date(first, second)
                time_parts = [int(part) for part in time_part.split(":")]
                while len(time_parts) < 3:
                    time_parts.append(0)
                return (
                    datetime(year, second, first, *time_parts),
                    f"{actual_label} time",
                )
            if label == "ISO datetime":
                normalized = text.replace(" ", "T")
                if normalized.count(":") == 1:
                    return datetime.strptime(normalized, "%Y-%m-%dT%H:%M"), "ISO datetime"
                return datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S"), "ISO datetime"
            fmt = DATE_FORMAT_MAP[label]
            if fmt:
                parsed = datetime.strptime(text, fmt)
                if label == "Month Day, Year":
                    parsed = datetime.strptime(text, "%b %d, %Y")
                value_date = parsed.date() if label in {"YYYY-MM-DD", "YYYY/MM/DD"} else parsed
                return value_date, label
        except (ValueError, OverflowError):
            return None
    return None


def _raw_kind(value: Any) -> str:
    if is_missing(value):
        return "missing"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "missing"
        if parse_boolean(text) is not None:
            return "boolean"
        if INTEGER_RE.fullmatch(text):
            return "integer"
        if FLOAT_RE.fullmatch(text) or SCIENTIFIC_RE.fullmatch(text) or GROUPED_NUMBER_RE.fullmatch(text):
            return "float"
        if parse_date_value(text) is not None:
            return "date"
        return "string"
    return "string"


def _normalized_kind_set(kind_counts: dict[str, int]) -> set[str]:
    kinds = {kind for kind, count in kind_counts.items() if count > 0}
    if "integer" in kinds or "float" in kinds:
        kinds.discard("integer")
        kinds.discard("float")
        kinds.add("numeric")
    if "date" in kinds or "datetime" in kinds:
        kinds.discard("datetime")
        kinds.add("date")
    return kinds


def infer_column_type(series: pd.Series, *, column_name: str = "") -> TypeInference:
    inference = TypeInference(detected_type="empty", primary_kind="empty")

    values = [value for value in series.tolist() if not is_missing(value)]
    total = len(values)
    inference.non_null_count = total
    if total == 0:
        return inference

    kind_counts: dict[str, int] = {}
    date_formats: dict[str, int] = {}
    leading_zero_count = 0
    whitespace_count = 0
    email_like_count = 0
    phone_like_count = 0
    nik_like_count = 0

    for value in values:
        kind = _raw_kind(value)
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

        if isinstance(value, str):
            if value != value.strip():
                whitespace_count += 1
            stripped = value.strip()
            if LEADING_ZERO_RE.fullmatch(stripped):
                leading_zero_count += 1
            if EMAIL_RE.fullmatch(stripped):
                email_like_count += 1
            if PHONE_RE.fullmatch(stripped):
                phone_like_count += 1
            if NIK_RE.fullmatch(stripped):
                nik_like_count += 1

        if kind == "date":
            parsed = parse_date_value(value)
            if parsed:
                date_formats[parsed[1]] = date_formats.get(parsed[1], 0) + 1
        elif isinstance(value, (date, datetime)):
            label = "Excel datetime" if isinstance(value, datetime) else "Excel date"
            date_formats[label] = date_formats.get(label, 0) + 1

    inference.kind_counts = kind_counts
    inference.date_formats = date_formats
    inference.leading_zero_count = leading_zero_count
    inference.whitespace_count = whitespace_count
    inference.email_like_count = email_like_count
    inference.phone_like_count = phone_like_count
    inference.nik_like_count = nik_like_count

    kinds = _normalized_kind_set(kind_counts)
    dominant = max(kind_counts.items(), key=lambda item: (item[1], item[0]))[0]
    if dominant in {"integer", "float"}:
        dominant = "numeric"
    if dominant == "datetime":
        dominant = "date"

    if len(kinds) == 1:
        kind = next(iter(kinds))
        detected = {
            "numeric": "float" if kind_counts.get("float") else "integer",
            "date": "datetime" if kind_counts.get("datetime") else "date",
            "boolean": "boolean",
            "string": "string",
        }.get(kind, "mixed")
        if detected == "float" and not kind_counts.get("float"):
            detected = "integer"
        matching = total
    else:
        matching_counts = {
            "numeric": kind_counts.get("integer", 0) + kind_counts.get("float", 0),
            "date": kind_counts.get("date", 0) + kind_counts.get("datetime", 0),
            "boolean": kind_counts.get("boolean", 0),
            "string": kind_counts.get("string", 0),
        }
        dominant_kind, matching = max(matching_counts.items(), key=lambda item: (item[1], item[0]))
        ratio = matching / total if total else 0.0
        if ratio >= 0.9:
            detected = {
                "numeric": "float" if kind_counts.get("float") else "integer",
                "date": "datetime" if kind_counts.get("datetime") else "date",
                "boolean": "boolean",
                "string": "string",
            }[dominant_kind]
            dominant = dominant_kind
        else:
            detected = "mixed"
            dominant = "mixed"

    if detected in {"integer", "float"} and leading_zero_count:
        detected = "string"
        dominant = "string"

    inference.detected_type = detected
    inference.primary_kind = dominant
    inference.matching_count = matching
    inference.mismatch_count = max(0, total - matching)

    unique_count = len({repr(value) for value in values})
    inference.high_cardinality = total >= 5 and unique_count / total >= 0.95
    name_hint = bool(
        re.search(r"(?:^|_)(?:id|uuid|guid|code|nik|nim|nip|account|reference|ref_no)(?:$|_)", column_name.casefold())
    )
    identifier_like = inference.high_cardinality and (
        name_hint
        or leading_zero_count > 0
        or (detected == "string" and total >= 5 and unique_count == total and name_hint)
    )
    inference.possible_identifier = bool(identifier_like)

    return inference


def numeric_values(series: pd.Series) -> list[float]:
    values: list[float] = []
    for value in series.tolist():
        number = parse_float(value)
        if number is not None:
            values.append(number)
    return values


def date_values(series: pd.Series) -> list[tuple[date | datetime, str]]:
    values: list[tuple[date | datetime, str]] = []
    for value in series.tolist():
        parsed = parse_date_value(value)
        if parsed:
            values.append(parsed)
    return values


def json_safe_number(value: float | None) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    return round(float(value), 6)


def non_null_strings(series: pd.Series) -> Iterable[str]:
    for value in series.tolist():
        if is_missing(value):
            continue
        if isinstance(value, str):
            yield value
        else:
            yield str(value)
