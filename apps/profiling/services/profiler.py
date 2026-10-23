"""Dataset and column profiling built on deterministic pandas operations."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from .inference import (
    TypeInference,
    date_values,
    infer_column_type,
    is_missing,
    json_safe_number,
    numeric_values,
    parse_boolean,
)

SENSITIVE_NAME_RE = re.compile(
    r"(?:e-?mail|phone|telephone|mobile|whatsapp|nik|ktp|passport|ssn|"
    r"credit.?card|card.?number|account.?number|iban|address|alamat|"
    r"full.?name|first.?name|last.?name|birth|dob|tanggal.?lahir|npwp)",
    re.IGNORECASE,
)


@dataclass
class DatasetMetrics:
    row_count: int
    column_count: int
    total_cells: int
    missing_cells: int
    missing_percentage: float
    duplicate_rows: int
    duplicate_percentage: float
    empty_rows: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "total_cells": self.total_cells,
            "missing_cells": self.missing_cells,
            "missing_percentage": round(self.missing_percentage, 2),
            "duplicate_rows": self.duplicate_rows,
            "duplicate_percentage": round(self.duplicate_percentage, 2),
            "empty_rows": self.empty_rows,
        }


@dataclass
class ProfiledColumn:
    column_name: str
    inference: TypeInference
    non_null_count: int
    null_count: int
    unique_count: int
    duplicate_value_count: int
    summary_statistics: dict[str, Any]
    inferred_patterns: list[str]
    top_values: list[dict[str, Any]] = field(default_factory=list)

    @property
    def null_percentage(self) -> float:
        total = self.non_null_count + self.null_count
        return round((self.null_count / total) * 100, 2) if total else 0.0


@dataclass
class ProfileResult:
    metrics: DatasetMetrics
    columns: list[ProfiledColumn]

    @property
    def column_map(self) -> dict[str, ProfiledColumn]:
        return {column.column_name: column for column in self.columns}


def compute_dataset_metrics(frame: pd.DataFrame) -> DatasetMetrics:
    row_count, column_count = frame.shape
    total_cells = int(row_count * column_count)
    if total_cells:
        missing_cells = int(frame.isna().to_numpy().sum())
        missing_pct = (missing_cells / total_cells) * 100
    else:
        missing_cells = 0
        missing_pct = 0.0
    duplicate_rows = int(frame.duplicated(keep="first").sum())
    duplicate_pct = (duplicate_rows / row_count) * 100 if row_count else 0.0
    empty_rows = int(frame.isna().all(axis=1).sum()) if row_count else 0
    return DatasetMetrics(
        row_count=int(row_count),
        column_count=int(column_count),
        total_cells=total_cells,
        missing_cells=missing_cells,
        missing_percentage=missing_pct,
        duplicate_rows=duplicate_rows,
        duplicate_percentage=duplicate_pct,
        empty_rows=empty_rows,
    )


def _is_sensitive_name(column_name: str) -> bool:
    return bool(SENSITIVE_NAME_RE.search(column_name))


def _safe_top_values(
    series: pd.Series,
    inference: TypeInference,
    column_name: str,
    *,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    if _is_sensitive_name(column_name):
        return [], True
    if inference.email_like_count or inference.phone_like_count or inference.nik_like_count:
        return [], True
    if inference.detected_type in {"integer", "float"} and inference.high_cardinality:
        return [], True

    values: list[str] = []
    for value in series.tolist():
        if is_missing(value):
            continue
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "…"
        values.append(text)
    if not values:
        return [], False
    top = Counter(values).most_common(limit)
    return [{"value": value, "count": count} for value, count in top], False


def _numeric_statistics(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    array = np.asarray(values, dtype=float)
    std = float(np.std(array, ddof=1)) if len(array) > 1 else None
    q1, q3 = np.percentile(array, [25, 75])
    return {
        "min": json_safe_number(float(np.min(array))),
        "max": json_safe_number(float(np.max(array))),
        "mean": json_safe_number(float(np.mean(array))),
        "median": json_safe_number(float(np.median(array))),
        "std_dev": json_safe_number(std),
        "q1": json_safe_number(float(q1)),
        "q3": json_safe_number(float(q3)),
        "count": len(values),
    }


def _date_statistics(values: list[tuple[date | datetime, str]]) -> dict[str, Any] | None:
    if not values:
        return None
    parsed_values = [item[0] for item in values]
    formats = sorted({item[1] for item in values})
    min_value = min(parsed_values)
    max_value = max(parsed_values)
    return {
        "min": min_value.isoformat(),
        "max": max_value.isoformat(),
        "formats": formats,
    }


def _boolean_statistics(series: pd.Series) -> dict[str, Any]:
    counts = {"true": 0, "false": 0}
    for value in series.tolist():
        parsed = parse_boolean(value)
        if parsed is True:
            counts["true"] += 1
        elif parsed is False:
            counts["false"] += 1
    return counts


def _text_statistics(series: pd.Series, top_values: list[dict[str, Any]], omitted: bool) -> dict[str, Any]:
    lengths = [len(str(value)) for value in series.tolist() if not is_missing(value)]
    return {
        "min_length": min(lengths) if lengths else None,
        "max_length": max(lengths) if lengths else None,
        "average_length": round(sum(lengths) / len(lengths), 2) if lengths else None,
        "top_values": top_values,
        "top_values_omitted": omitted,
    }


def _build_patterns(
    column_name: str,
    profile_data: ProfiledColumn,
    dataset_metrics: DatasetMetrics,
) -> list[str]:
    inference = profile_data.inference
    patterns: set[str] = set()
    if inference.possible_identifier:
        patterns.add("possible_identifier")
    if inference.high_cardinality:
        patterns.add("high_cardinality")
    if inference.leading_zero_count:
        patterns.add("leading_zero_values")
    if inference.email_like_count:
        patterns.add("email_like")
    if inference.phone_like_count:
        patterns.add("phone_like")
    if inference.nik_like_count:
        patterns.add("nik_like")
    if inference.whitespace_count:
        patterns.add("inconsistent_whitespace")
    if inference.detected_type == "mixed" or inference.mismatch_count:
        patterns.add("mixed_types")
    if len(inference.date_formats) > 1:
        patterns.add("multiple_date_formats")
    if any("ambiguous" in label.casefold() for label in inference.date_formats):
        patterns.add("ambiguous_day_month")
    if profile_data.non_null_count and profile_data.unique_count == 1:
        patterns.add("constant")
    elif (
        dataset_metrics.row_count >= 20
        and profile_data.non_null_count
        and profile_data.unique_count / profile_data.non_null_count <= 0.05
    ):
        patterns.add("near_constant")
    if _is_sensitive_name(column_name):
        patterns.add("sensitive_name_hint")
    return sorted(patterns)


def profile_column(series: pd.Series, column_name: str, dataset_metrics: DatasetMetrics) -> ProfiledColumn:
    inference = infer_column_type(series, column_name=column_name)
    non_null_count = int(series.notna().sum())
    null_count = int(len(series) - non_null_count)
    if non_null_count:
        try:
            unique_count = int(series.nunique(dropna=True))
        except TypeError:
            unique_count = len({repr(value) for value in series.tolist() if not is_missing(value)})
    else:
        unique_count = 0
    duplicate_value_count = max(0, non_null_count - unique_count)

    top_values, omitted = _safe_top_values(series, inference, column_name)
    statistics: dict[str, Any] = {
        "null_percentage": round((null_count / len(series)) * 100, 2) if len(series) else 0.0,
    }

    if inference.detected_type in {"integer", "float"}:
        stats = _numeric_statistics(numeric_values(series))
        if stats:
            statistics["numeric"] = stats
    elif inference.detected_type in {"date", "datetime"}:
        stats = _date_statistics(date_values(series))
        if stats:
            statistics["datetime"] = stats
    elif inference.detected_type == "boolean":
        statistics["boolean"] = _boolean_statistics(series)

    statistics["text"] = _text_statistics(series, top_values, omitted)
    if inference.date_formats:
        statistics["date_formats"] = dict(sorted(inference.date_formats.items()))

    profile = ProfiledColumn(
        column_name=column_name,
        inference=inference,
        non_null_count=non_null_count,
        null_count=null_count,
        unique_count=unique_count,
        duplicate_value_count=duplicate_value_count,
        summary_statistics=statistics,
        inferred_patterns=[],
        top_values=top_values,
    )
    profile.inferred_patterns = _build_patterns(column_name, profile, dataset_metrics)
    return profile


def profile_frame(frame: pd.DataFrame) -> ProfileResult:
    metrics = compute_dataset_metrics(frame)
    columns = [
        profile_column(frame[column_name], str(column_name), metrics)
        for column_name in frame.columns
    ]
    return ProfileResult(metrics=metrics, columns=columns)


def safe_float(value: float | None) -> float | None:
    if value is None:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return round(float(value), 6)
