"""Deterministic data-quality rules DS001-DS010.

Every rule returns findings that reference row positions and counts. Raw
sensitive values are never copied into explanations or recommendations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from apps.profiling.services.inference import (
    parse_boolean,
    parse_date_value,
    parse_float,
    parse_integer,
)
from apps.profiling.services.profiler import DatasetMetrics, ProfileResult, ProfiledColumn

MAX_SAMPLE_POSITIONS = 20

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
NIK_RE = re.compile(r"(?<!\d)\d{16}(?!\d)")
INTERNATIONAL_PHONE_RE = re.compile(r"\+[1-9][0-9\s().\-]{7,17}[0-9]")
ID_PHONE_RE = re.compile(r"(?<!\d)(?:\+?62|0)8[0-9\s().\-]{7,14}(?<![\d\s().\-])")
COLUMN_NORMALIZER_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class IssueFinding:
    column_name: str
    rule_id: str
    severity: str
    issue_type: str
    issue_count: int
    explanation: str
    recommendation: str
    sample_positions: list[int] = field(default_factory=list)


@dataclass
class RuleContext:
    frame: pd.DataFrame
    metrics: DatasetMetrics
    profile: ProfileResult
    original_headers: list[str]

    @property
    def column_map(self) -> dict[str, ProfiledColumn]:
        return self.profile.column_map

    @property
    def rows(self) -> int:
        return max(self.metrics.row_count, 1)


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    name: str
    description: str
    default_severity: str
    check: Callable[[RuleContext], list[IssueFinding]]


def _positions(mask: pd.Series, limit: int = MAX_SAMPLE_POSITIONS) -> list[int]:
    indexes = np.flatnonzero(mask.fillna(False).to_numpy())
    return [int(index) + 1 for index in indexes[:limit]]


def _safe_column_name(column_name: str) -> str:
    return str(column_name)[:300]


def _severity_for_ratio(ratio: float, *, high: float, medium: float) -> str:
    if ratio >= high:
        return "high"
    if ratio >= medium:
        return "medium"
    return "low"


def check_missing_values(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name, column in context.column_map.items():
        if column.null_count <= 0:
            continue
        mask = context.frame[column_name].isna()
        ratio = column.null_count / context.rows
        findings.append(
            IssueFinding(
                column_name=_safe_column_name(column_name),
                rule_id="DS001",
                severity=_severity_for_ratio(ratio, high=0.5, medium=0.1),
                issue_type="missing_values",
                issue_count=column.null_count,
                explanation=(
                    f"{column.null_count:,} of {column.null_count + column.non_null_count:,} values "
                    f"are missing ({ratio * 100:.1f}%). Empty strings, whitespace-only values, "
                    "NA, N/A, NULL, None, and NaN are treated as missing."
                ),
                recommendation=(
                    "Choose a deliberate strategy in the cleaning workspace: keep values, "
                    "drop affected rows, fill with a constant, mean, median, or mode."
                ),
                sample_positions=_positions(mask),
            )
        )
    return findings


def check_duplicate_rows(context: RuleContext) -> list[IssueFinding]:
    duplicate_count = context.metrics.duplicate_rows
    if duplicate_count <= 0:
        return []
    mask = context.frame.duplicated(keep=False)
    ratio = duplicate_count / context.rows
    return [
        IssueFinding(
            column_name="",
            rule_id="DS002",
            severity=_severity_for_ratio(ratio, high=0.2, medium=0.05),
            issue_type="duplicate_rows",
            issue_count=int(duplicate_count),
            explanation=(
                f"{duplicate_count:,} row(s) are exact duplicates of an earlier row "
                f"({ratio * 100:.1f}% of all rows). Sample positions include every row "
                "that participates in a duplicate group."
            ),
            recommendation=(
                "Review the rows and optionally remove exact duplicates in the cleaning "
                "workspace. Duplicate rows are never removed automatically."
            ),
            sample_positions=_positions(mask),
        )
    ]


def _normalize_header(header: str) -> str:
    return COLUMN_NORMALIZER_RE.sub("_", header.strip().casefold()).strip("_")


def check_duplicate_column_names(context: RuleContext) -> list[IssueFinding]:
    groups: dict[str, list[str]] = {}
    for header in context.original_headers:
        normalized = _normalize_header(header)
        if not normalized:
            normalized = "(blank)"
        groups.setdefault(normalized, []).append(header)
    conflicts = {key: values for key, values in groups.items() if len(values) > 1}
    if not conflicts:
        return []

    group_labels = list(conflicts.keys())[:5]
    affected = sum(len(values) for values in conflicts.values())
    return [
        IssueFinding(
            column_name="",
            rule_id="DS003",
            severity="medium",
            issue_type="duplicate_column_names",
            issue_count=affected,
            explanation=(
                f"{affected} header(s) collapse into {len(conflicts)} conflicting normalized "
                f"name group(s). Examples of normalized groups: {', '.join(group_labels)}."
            ),
            recommendation=(
                "Normalize column names or rename conflicting headers before relying on "
                "downstream formulas. The parser keeps unique working names, but ambiguous "
                "source headers are a structural risk."
            ),
            sample_positions=[],
        )
    ]


def check_whitespace(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name in context.frame.columns:
        series = context.frame[column_name]
        mask = series.map(
            lambda value: isinstance(value, str) and value != value.strip() if not pd.isna(value) else False
        )
        count = int(mask.sum())
        if count:
            findings.append(
                IssueFinding(
                    column_name=_safe_column_name(column_name),
                    rule_id="DS004",
                    severity="low",
                    issue_type="unnecessary_whitespace",
                    issue_count=count,
                    explanation=(
                        f"{count:,} text value(s) contain leading or trailing whitespace. "
                        "Whitespace can create duplicate categories and break exact matching."
                    ),
                    recommendation="Review and apply the trim-whitespace cleaning operation.",
                    sample_positions=_positions(mask),
                )
            )
    return findings


def _value_matches_kind(value: object, kind: str) -> bool:
    if kind == "integer":
        return parse_integer(value) is not None
    if kind == "float":
        return parse_float(value) is not None
    if kind == "boolean":
        return parse_boolean(value) is not None
    if kind == "date":
        return parse_date_value(value) is not None
    if kind == "string":
        return isinstance(value, str)
    return True


def check_inconsistent_types(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name, column in context.column_map.items():
        inference = column.inference
        if inference.mismatch_count <= 0:
            continue
        if inference.detected_type in {"date", "datetime"}:
            continue
        kind = inference.primary_kind
        if kind == "mixed":
            kind = "string" if inference.kind_counts.get("string", 0) else "integer"
        mask = context.frame[column_name].map(
            lambda value: (
                False if pd.isna(value) else not _value_matches_kind(value, kind)
            )
        )
        count = int(mask.sum())
        if count <= 0:
            continue
        ratio = count / context.rows
        findings.append(
            IssueFinding(
                column_name=_safe_column_name(column_name),
                rule_id="DS005",
                severity=_severity_for_ratio(ratio, high=0.25, medium=0.05),
                issue_type="inconsistent_value_types",
                issue_count=count,
                explanation=(
                    f"{count:,} value(s) do not match the column's dominant "
                    f"{kind} type. Mixed representations can make sorting, aggregation, "
                    "and conversion unreliable."
                ),
                recommendation=(
                    "Inspect these rows and convert the column only after deciding how "
                    "non-conforming values should be handled."
                ),
                sample_positions=_positions(mask),
            )
        )
    return findings


def check_inconsistent_categories(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name, column in context.column_map.items():
        inference = column.inference
        if inference.detected_type not in {"string", "boolean"}:
            continue
        if inference.possible_identifier or column.unique_count < 2:
            continue
        if column.unique_count > max(60, int(context.rows * 0.25)):
            continue
        if inference.email_like_count or inference.phone_like_count or inference.nik_like_count:
            continue

        variants: dict[str, set[str]] = {}
        positions_by_variant: dict[str, list[int]] = {}
        for position, value in enumerate(context.frame[column_name].tolist(), start=1):
            if not isinstance(value, str) or not value.strip():
                continue
            normalized = re.sub(r"\s+", " ", value.strip().casefold())
            variants.setdefault(normalized, set()).add(value)
            positions_by_variant.setdefault(normalized, []).append(position)

        conflicting = {
            key: variants_for_key
            for key, variants_for_key in variants.items()
            if len(variants_for_key) > 1
        }
        if not conflicting:
            continue

        conflicting_positions: list[int] = []
        for key in conflicting:
            conflicting_positions.extend(positions_by_variant[key])
        conflicting_positions.sort()
        count = len(conflicting_positions)
        findings.append(
            IssueFinding(
                column_name=_safe_column_name(column_name),
                rule_id="DS006",
                severity="low",
                issue_type="inconsistent_categories",
                issue_count=count,
                explanation=(
                    f"{count:,} value(s) belong to {len(conflicting)} normalized categor"
                    f"{'y' if len(conflicting) == 1 else 'ies'} with multiple spelling or "
                    "capitalization variants. Raw category values are not copied into this report."
                ),
                recommendation=(
                    "Review category variants and use an explicit user-defined mapping in "
                    "the cleaning workspace. Similar-looking values are not merged automatically."
                ),
                sample_positions=conflicting_positions[:MAX_SAMPLE_POSITIONS],
            )
        )
    return findings


def check_date_formats(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name, column in context.column_map.items():
        inference = column.inference
        if not inference.date_formats:
            continue
        if inference.detected_type not in {"date", "datetime", "mixed", "string"}:
            continue
        series = context.frame[column_name]
        failure_mask = series.map(
            lambda value: (
                False
                if pd.isna(value)
                else parse_date_value(value) is None
            )
        )
        failures = int(failure_mask.sum())
        formats = inference.date_formats
        dominant_format = max(formats.items(), key=lambda item: (item[1], item[0]))[0]
        minority_mask = series.map(
            lambda value: (
                False
                if pd.isna(value)
                else (
                    parse_date_value(value) is not None
                    and (parse_date_value(value) or (None, ""))[1] != dominant_format
                )
            )
        )
        minority_count = int(minority_mask.sum())
        issues = failures + minority_count
        if issues <= 0:
            continue
        multiple_formats = len(formats) > 1
        severity = "medium" if failures else "low"
        format_list = ", ".join(sorted(formats))
        findings.append(
            IssueFinding(
                column_name=_safe_column_name(column_name),
                rule_id="DS007",
                severity=severity,
                issue_type="date_format_issues",
                issue_count=issues,
                explanation=(
                    f"{failures:,} value(s) fail date parsing; {len(formats)} date "
                    f"format pattern(s) detected ({format_list}). Day/month ambiguity is "
                    "reported but not silently resolved."
                ),
                recommendation=(
                    "Confirm the intended date format, inspect ambiguous values, then "
                    "convert with an explicit format in the cleaning workspace."
                ),
                sample_positions=_positions(failure_mask | minority_mask),
            )
        )
    return findings


def _outlier_mask(values: pd.Series) -> tuple[pd.Series, float, float] | None:
    numeric = values.map(parse_float)
    valid_mask = numeric.notna()
    if int(valid_mask.sum()) < 4:
        return None
    array = numeric[valid_mask].astype(float)
    q1 = float(np.percentile(array, 25))
    q3 = float(np.percentile(array, 75))
    iqr = q3 - q1
    if iqr <= 0:
        return None
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    mask = numeric.map(
        lambda value: bool(value < lower or value > upper) if pd.notna(value) else False
    )
    return mask, lower, upper


def check_potential_outliers(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name, column in context.column_map.items():
        if column.inference.detected_type not in {"integer", "float"}:
            continue
        if column.inference.possible_identifier:
            continue
        result = _outlier_mask(context.frame[column_name])
        if result is None:
            continue
        mask, lower, upper = result
        count = int(mask.sum())
        if count <= 0:
            continue
        findings.append(
            IssueFinding(
                column_name=_safe_column_name(column_name),
                rule_id="DS008",
                severity="info",
                issue_type="potential_outliers",
                issue_count=count,
                explanation=(
                    f"{count:,} value(s) fall outside 1.5×IQR fences "
                    f"({lower:.2f} to {upper:.2f}). Outliers are not automatically errors; "
                    "they may be valid extremes, typos, or a different population."
                ),
                recommendation=(
                    "Inspect these values before changing them. DataSieve does not remove "
                    "outliers automatically and does not lower the quality score for this finding."
                ),
                sample_positions=_positions(mask),
            )
        )
    return findings


def _luhn_valid(text: str) -> bool:
    digits = [int(character) for character in re.sub(r"\D", "", text)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _sensitive_types(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    text = value.strip()
    if not text:
        return set()
    found: set[str] = set()
    if EMAIL_RE.search(text):
        found.add("email")
    phone_normalized = re.sub(r"[^\d+]", "", text)
    if NIK_RE.search(text):
        found.add("national_id")
    elif _luhn_valid(text):
        found.add("payment_card")
    elif INTERNATIONAL_PHONE_RE.fullmatch(phone_normalized) or ID_PHONE_RE.fullmatch(text):
        found.add("phone")
    return found


def check_potential_sensitive_data(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name in context.frame.columns:
        matched_positions: list[int] = []
        all_types: set[str] = set()
        for position, value in enumerate(context.frame[column_name].tolist(), start=1):
            types = _sensitive_types(value)
            if types:
                matched_positions.append(position)
                all_types.update(types)
        if not matched_positions:
            continue
        findings.append(
            IssueFinding(
                column_name=_safe_column_name(column_name),
                rule_id="DS009",
                severity="high",
                issue_type="potential_sensitive_data",
                issue_count=len(matched_positions),
                explanation=(
                    f"{len(matched_positions):,} cell(s) match supported heuristics for "
                    f"{', '.join(sorted(all_types))}. Matching values are not copied into "
                    "this report. Detection is heuristic and cannot guarantee that all "
                    "personal or sensitive data has been found."
                ),
                recommendation=(
                    "Review the column, use masking or pseudonymization where appropriate, "
                    "and verify the result against your own privacy requirements. Masking "
                    "is not a formal anonymization guarantee."
                ),
                sample_positions=matched_positions[:MAX_SAMPLE_POSITIONS],
            )
        )
    return findings


def check_constant_columns(context: RuleContext) -> list[IssueFinding]:
    findings: list[IssueFinding] = []
    for column_name, column in context.column_map.items():
        if column.non_null_count <= 0:
            continue
        unique = column.unique_count
        ratio = unique / column.non_null_count if column.non_null_count else 1.0
        is_constant = unique == 1
        near_constant = False
        if not is_constant and context.metrics.row_count >= 20 and ratio <= 0.05:
            near_constant = True
        if not is_constant and not near_constant and context.metrics.row_count >= 20:
            try:
                top_share = context.frame[column_name].value_counts(dropna=True).iloc[0] / column.non_null_count
                near_constant = bool(top_share >= 0.95)
            except (IndexError, KeyError):
                near_constant = False
        if not is_constant and not near_constant:
            continue
        findings.append(
            IssueFinding(
                column_name=_safe_column_name(column_name),
                rule_id="DS010",
                severity="medium" if is_constant else "low",
                issue_type="constant_column" if is_constant else "near_constant_column",
                issue_count=column.non_null_count,
                explanation=(
                    "The column has a single non-missing value."
                    if is_constant
                    else "The column has very low variation and may carry little analytical signal."
                ),
                recommendation=(
                    "Confirm that this column is expected. Constant columns can be dropped "
                    "from analysis, but DataSieve will not remove them automatically."
                ),
                sample_positions=[],
            )
        )
    return findings


RULES: tuple[RuleDefinition, ...] = (
    RuleDefinition("DS001", "Missing values", "Missing or empty cells by column.", "medium", check_missing_values),
    RuleDefinition("DS002", "Duplicate rows", "Exact duplicate records.", "medium", check_duplicate_rows),
    RuleDefinition("DS003", "Duplicate column names", "Headers conflicting after normalization.", "medium", check_duplicate_column_names),
    RuleDefinition("DS004", "Whitespace issues", "Leading or trailing whitespace in text.", "low", check_whitespace),
    RuleDefinition("DS005", "Inconsistent types", "Values not matching the dominant type.", "medium", check_inconsistent_types),
    RuleDefinition("DS006", "Inconsistent categories", "Category spelling/case variants.", "low", check_inconsistent_categories),
    RuleDefinition("DS007", "Date format issues", "Unparseable or inconsistent dates.", "medium", check_date_formats),
    RuleDefinition("DS008", "Potential outliers", "Values outside 1.5×IQR fences.", "info", check_potential_outliers),
    RuleDefinition("DS009", "Potential sensitive data", "Heuristic email, phone, ID, or card patterns.", "high", check_potential_sensitive_data),
    RuleDefinition("DS010", "Constant/near-constant columns", "Columns with very low variation.", "low", check_constant_columns),
)

RULE_MAP: dict[str, RuleDefinition] = {rule.rule_id: rule for rule in RULES}


def evaluate_rules(
    frame: pd.DataFrame,
    metrics: DatasetMetrics,
    profile: ProfileResult,
    original_headers: list[str],
) -> list[IssueFinding]:
    context = RuleContext(
        frame=frame,
        metrics=metrics,
        profile=profile,
        original_headers=original_headers,
    )
    findings: list[IssueFinding] = []
    for rule in RULES:
        findings.extend(rule.check(context))
    return findings


def iter_rule_findings_by_column(findings: Iterable[IssueFinding]) -> dict[str, list[IssueFinding]]:
    grouped: dict[str, list[IssueFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.column_name, []).append(finding)
    return grouped
