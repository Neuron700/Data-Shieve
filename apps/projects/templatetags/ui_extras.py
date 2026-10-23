from __future__ import annotations

import json
from typing import Any

from django import template

register = template.Library()

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

STATUS_TONES = {
    "ready": "success",
    "analyzing": "warning",
    "failed": "danger",
    "pending": "warning",
    "running": "info",
    "completed": "success",
    "previewed": "info",
    "applied": "success",
    "discarded": "neutral",
    "open": "warning",
    "acknowledged": "info",
    "resolved": "success",
}


@register.filter
def get_item(mapping: Any, key: Any) -> Any:
    if mapping is None:
        return None
    try:
        return mapping[key]
    except (KeyError, IndexError, TypeError):
        return None


@register.filter
def score_tone(score: Any) -> str:
    if not isinstance(score, (int, float)):
        return "muted"
    if score >= 90:
        return "strong"
    if score >= 75:
        return "good"
    if score >= 50:
        return "review"
    return "poor"


@register.filter
def score_category(score: Any) -> str:
    return {
        "strong": "Strong",
        "good": "Good",
        "review": "Needs review",
        "poor": "Poor",
        "muted": "Not scored",
    }[score_tone(score)]


@register.filter
def status_tone(value: Any) -> str:
    return STATUS_TONES.get(str(value).lower(), "neutral")


@register.filter
def type_label(value: Any) -> str:
    return TYPE_LABELS.get(str(value).lower(), str(value).replace("_", " ").title())


@register.filter
def token_label(value: Any) -> str:
    return str(value or "").replace("_", " ").replace("-", " ").replace(".", " ").strip().title()


@register.filter
def share_of(value: Any, total: Any) -> float:
    try:
        total_value = float(total)
        if total_value <= 0:
            return 0.0
        return round(min(100.0, max(0.0, (float(value) / total_value) * 100)), 1)
    except (TypeError, ValueError):
        return 0.0


@register.filter
def percent(value: Any, digits: int = 1) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.{int(digits)}f}%"
    except (TypeError, ValueError):
        return "—"


@register.filter
def plain_number(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


@register.filter
def config_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "—"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


@register.filter
def json_display(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


@register.filter
def subtract(value: Any, argument: Any) -> int:
    try:
        return int(value) - int(argument)
    except (TypeError, ValueError):
        return 0


@register.filter
def page_window(page: Any, radius: int = 2) -> list[Any]:
    try:
        current = int(page.number)
        total = int(page.paginator.num_pages)
    except (AttributeError, TypeError, ValueError):
        return []
    if total <= 7:
        return list(range(1, total + 1))
    radius = max(1, int(radius))
    start = max(2, current - radius)
    end = min(total - 1, current + radius)
    values: list[Any] = [1]
    if start > 2:
        values.append(None)
    values.extend(range(start, end + 1))
    if end < total - 1:
        values.append(None)
    values.append(total)
    return values


@register.filter
def initials(value: Any) -> str:
    parts = [part for part in str(value or "").replace("_", " ").split() if part]
    if not parts:
        return "DS"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()
