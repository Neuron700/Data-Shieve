"""Pure, testable cleaning operations.

Operations accept a configuration dict, validate it, and return a new
DataFrame. The input frame is never mutated. No configuration value is ever
evaluated as code.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean as python_mean
from statistics import median as python_median
from typing import Any, Callable

import pandas as pd

from apps.datasets.services.privacy import mask_email_text, mask_phone_text, pseudonymize
from apps.profiling.services.inference import (
    infer_column_type,
    is_missing,
    parse_boolean,
    parse_date_value,
    parse_float,
    parse_integer,
)

MAX_COLUMNS_PER_OPERATION = 100
MAX_CATEGORY_MAPPINGS = 500


class CleaningValidationError(ValueError):
    """Safe, user-facing validation error for a cleaning operation."""


@dataclass(frozen=True)
class OperationDefinition:
    operation: str
    label: str
    description: str
    requires_columns: bool = False
    accepts_columns: bool = True
    sensitive: bool = False
    note: str = ""


@dataclass
class OperationResult:
    frame: pd.DataFrame
    column_mapping: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


OPERATIONS: dict[str, OperationDefinition] = {
    "trim_whitespace": OperationDefinition(
        "trim_whitespace",
        "Trim whitespace",
        "Remove leading and trailing whitespace from text values.",
        note="Only string cells are changed. Values inside the text are preserved.",
    ),
    "normalize_column_names": OperationDefinition(
        "normalize_column_names",
        "Normalize column names",
        "Convert headers to lowercase snake_case and resolve collisions.",
        accepts_columns=False,
        note="Downstream references to old column names may need updating.",
    ),
    "drop_empty_rows": OperationDefinition(
        "drop_empty_rows",
        "Drop entirely empty rows",
        "Remove rows where every cell is missing.",
        accepts_columns=False,
        note="This permanently removes rows from the new version; the source stays intact.",
    ),
    "drop_duplicates": OperationDefinition(
        "drop_duplicates",
        "Drop exact duplicate rows",
        "Remove duplicate rows, optionally considering selected columns only.",
        note="This permanently removes rows from the new version; the source stays intact.",
    ),
    "handle_missing": OperationDefinition(
        "handle_missing",
        "Handle missing values",
        "Apply an explicit fill or row-removal strategy to selected columns.",
        requires_columns=True,
        note="Filling values changes the dataset's distribution and must be documented.",
    ),
    "convert_type": OperationDefinition(
        "convert_type",
        "Convert data type",
        "Convert selected columns to text, integer, float, date, or boolean.",
        requires_columns=True,
        note="Conversion is rejected when any non-missing value cannot be converted.",
    ),
    "normalize_categories": OperationDefinition(
        "normalize_categories",
        "Map categories",
        "Replace exact category values using a user-defined mapping.",
        requires_columns=True,
        note="Mapping can merge categories that are semantically different. Review the map.",
    ),
    "mask_email": OperationDefinition(
        "mask_email",
        "Mask email addresses",
        "Partially mask email addresses found in selected columns.",
        requires_columns=True,
        sensitive=True,
        note="Masking is not a formal anonymization guarantee; verify it for your use case.",
    ),
    "mask_phone": OperationDefinition(
        "mask_phone",
        "Mask phone numbers",
        "Mask all but the last four digits of phone-like values.",
        requires_columns=True,
        sensitive=True,
        note="Masking is not a formal anonymization guarantee; verify it for your use case.",
    ),
    "mask_identifier": OperationDefinition(
        "mask_identifier",
        "Pseudonymize identifiers",
        "Replace each non-missing identifier with a salted hash pseudonym.",
        requires_columns=True,
        sensitive=True,
        note="Pseudonyms are consistent within a job but cannot be reversed. This is not a legal anonymization guarantee.",
    ),
}


def _require_frame(frame: pd.DataFrame) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise CleaningValidationError("A tabular dataset is required.")


def _selected_columns(
    frame: pd.DataFrame,
    config: dict,
    *,
    required: bool = False,
    default_all: bool = False,
) -> list[str]:
    raw = config.get("columns", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise CleaningValidationError("Columns must be provided as a list.")
    if len(raw) > MAX_COLUMNS_PER_OPERATION:
        raise CleaningValidationError(
            f"At most {MAX_COLUMNS_PER_OPERATION} columns can be processed per operation."
        )
    columns: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise CleaningValidationError("Column names must be text.")
        if item in columns:
            continue
        columns.append(item)
    if required and not columns:
        raise CleaningValidationError("Select at least one column.")
    if default_all and not columns:
        columns = [str(column) for column in frame.columns]
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise CleaningValidationError(
            "One or more selected columns no longer exist in this version."
        )
    return columns


def _empty_mask(series: pd.Series) -> pd.Series:
    return series.map(is_missing)


def _apply_trim(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config, default_all=True)
    result = frame.copy(deep=True)
    for column in columns:
        result[column] = result[column].map(
            lambda value: value.strip() if isinstance(value, str) else value
        )
    return OperationResult(result)


def _snake_case(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    candidate = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text.strip().lower()).strip("_")
    if not candidate:
        candidate = "column"
    if candidate[0].isdigit():
        candidate = f"column_{candidate}"
    return candidate[:80]


def _apply_normalize_columns(frame: pd.DataFrame, config: dict) -> OperationResult:
    result = frame.copy(deep=True)
    used: set[str] = set()
    mapping: dict[str, str] = {}
    new_columns: list[str] = []
    for index, original in enumerate(result.columns):
        base = _snake_case(str(original)) or f"column_{index + 1}"
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        new_columns.append(candidate)
        if candidate != str(original):
            mapping[str(original)] = candidate
    result.columns = new_columns
    return OperationResult(result, column_mapping=mapping)


def _apply_drop_empty(frame: pd.DataFrame, config: dict) -> OperationResult:
    result = frame.dropna(how="all").reset_index(drop=True)
    return OperationResult(result)


def _apply_drop_duplicates(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config)
    subset = columns or None
    result = frame.drop_duplicates(subset=subset, keep="first").reset_index(drop=True)
    return OperationResult(result)


def _fill_constant(series: pd.Series, column: str, constant: Any) -> pd.Series:
    if constant is None or (isinstance(constant, str) and not constant.strip()):
        raise CleaningValidationError("Enter a non-empty constant value.")
    inference = infer_column_type(series, column_name=column)
    result = series.copy(deep=True)
    missing_mask = _empty_mask(result)

    if not missing_mask.any():
        return result

    if inference.detected_type in {"integer", "float"}:
        number = parse_float(constant)
        if number is None:
            raise CleaningValidationError(
                f"The column '{column}' is numeric; the fill constant must be numeric."
            )
        fill_value: Any = number
    elif inference.detected_type in {"date", "datetime"}:
        parsed = parse_date_value(constant)
        if parsed is None:
            raise CleaningValidationError(
                f"The column '{column}' is date-like; the fill constant must be a supported date."
            )
        fill_value = parsed[0]
    elif inference.detected_type == "boolean":
        parsed_bool = parse_boolean(constant)
        if parsed_bool is None:
            raise CleaningValidationError(
                f"The column '{column}' is boolean-like; use true/false or yes/no."
            )
        fill_value = parsed_bool
    else:
        fill_value = str(constant)

    result.loc[missing_mask] = fill_value
    return result


def _apply_handle_missing(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config, required=True)
    strategy = str(config.get("strategy", "")).strip().lower()
    allowed = {"drop_rows", "constant", "mean", "median", "mode"}
    if strategy not in allowed:
        raise CleaningValidationError("Choose a supported missing-value strategy.")

    result = frame.copy(deep=True)
    notes: list[str] = []

    if strategy == "drop_rows":
        mask = result[columns].map(is_missing).any(axis=1)
        result = result.loc[~mask].reset_index(drop=True)
        return OperationResult(result, notes=["Rows containing missing values in any selected column were dropped."])

    if strategy == "constant":
        constant = config.get("constant_value")
        for column in columns:
            result[column] = _fill_constant(result[column], column, constant)
        return OperationResult(result, notes=["Missing values were replaced with the supplied constant."])

    for column in columns:
        inference = infer_column_type(result[column], column_name=column)
        if strategy in {"mean", "median"}:
            if inference.detected_type not in {"integer", "float"}:
                raise CleaningValidationError(
                    f"The column '{column}' is not numeric. Mean/median fill is not allowed."
                )
            values = [
                parse_float(value)
                for value in result[column].tolist()
                if parse_float(value) is not None
            ]
            if not values:
                raise CleaningValidationError(
                    f"The column '{column}' has no numeric values to derive a fill value."
                )
            fill_value = python_mean(values) if strategy == "mean" else python_median(values)
        else:
            mode = result[column].dropna().mode(dropna=True)
            if mode.empty:
                raise CleaningValidationError(
                    f"The column '{column}' has no non-missing values to derive a mode."
                )
            fill_value = mode.iloc[0]
        mask = _empty_mask(result[column])
        if mask.any():
            result.loc[mask, column] = fill_value
    notes.append(f"Missing values were filled using the {strategy} strategy.")
    return OperationResult(result, notes=notes)


def _convert_single(value: Any, target_type: str) -> Any:
    if is_missing(value):
        return pd.NA
    if target_type == "string":
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    if target_type == "integer":
        parsed = parse_integer(value)
        if parsed is None:
            raise ValueError
        return parsed
    if target_type == "float":
        parsed = parse_float(value)
        if parsed is None:
            raise ValueError
        return parsed
    if target_type == "date":
        parsed = parse_date_value(value)
        if parsed is None:
            raise ValueError
        parsed_value = parsed[0]
        if isinstance(parsed_value, datetime):
            return parsed_value.isoformat(sep=" ")
        return parsed_value.isoformat()
    if target_type == "boolean":
        parsed = parse_boolean(value)
        if parsed is None:
            raise ValueError
        return parsed
    raise CleaningValidationError("Unsupported target type.")


def _apply_convert_type(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config, required=True)
    target_type = str(config.get("target_type", "")).strip().lower()
    if target_type not in {"string", "integer", "float", "date", "boolean"}:
        raise CleaningValidationError("Choose a supported target type.")
    result = frame.copy(deep=True)
    for column in columns:
        converted: list[Any] = []
        invalid = 0
        for value in result[column].tolist():
            try:
                converted.append(_convert_single(value, target_type))
            except ValueError:
                invalid += 1
                converted.append(pd.NA)
        if invalid:
            raise CleaningValidationError(
                f"{invalid} non-missing value(s) in '{column}' cannot be converted to "
                f"{target_type}. Resolve them first; no values were changed."
            )
        result[column] = pd.Series(converted, index=result.index, dtype=object)
    return OperationResult(result, notes=[f"Selected columns were converted to {target_type}."])


def _apply_normalize_categories(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config, required=True)
    if len(columns) != 1:
        raise CleaningValidationError("Category mapping applies to exactly one column.")
    column = columns[0]
    mapping = config.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise CleaningValidationError("Provide a non-empty category mapping.")
    if len(mapping) > MAX_CATEGORY_MAPPINGS:
        raise CleaningValidationError(
            f"At most {MAX_CATEGORY_MAPPINGS} category mappings are allowed per operation."
        )
    clean_mapping: dict[str, str] = {}
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise CleaningValidationError("Category mapping keys and values must be text.")
        if not key or not value:
            raise CleaningValidationError("Category mapping keys and values cannot be empty.")
        clean_mapping[key] = value
    result = frame.copy(deep=True)
    result[column] = result[column].map(
        lambda value: clean_mapping.get(value, value) if isinstance(value, str) else value
    )
    return OperationResult(
        result,
        notes=["Only exact category matches were replaced."],
    )


def _apply_mask_email(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config, required=True)
    result = frame.copy(deep=True)
    for column in columns:
        result[column] = result[column].map(
            lambda value: mask_email_text(value) if isinstance(value, str) else value
        )
    return OperationResult(
        result,
        notes=["Email local parts were partially masked. Domains are preserved."],
    )


def _apply_mask_phone(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config, required=True)
    result = frame.copy(deep=True)
    for column in columns:
        result[column] = result[column].map(
            lambda value: mask_phone_text(value) if isinstance(value, str) else value
        )
    return OperationResult(
        result,
        notes=["Phone-like sequences were partially masked, keeping the last four digits."],
    )


def _apply_mask_identifier(frame: pd.DataFrame, config: dict) -> OperationResult:
    columns = _selected_columns(frame, config, required=True)
    salt = str(config.get("salt", ""))
    prefix = str(config.get("prefix", "ID-"))[:12]
    if not salt:
        raise CleaningValidationError("The pseudonymization configuration is incomplete.")
    result = frame.copy(deep=True)
    for column in columns:
        result[column] = result[column].map(
            lambda value: pseudonymize(value, salt, prefix) if not is_missing(value) else pd.NA
        )
    return OperationResult(
        result,
        notes=[
            "Identifier values were replaced with salted pseudonyms. Identical values map to identical pseudonyms within this job."
        ],
    )


HANDLERS: dict[str, Callable[[pd.DataFrame, dict], OperationResult]] = {
    "trim_whitespace": _apply_trim,
    "normalize_column_names": _apply_normalize_columns,
    "drop_empty_rows": _apply_drop_empty,
    "drop_duplicates": _apply_drop_duplicates,
    "handle_missing": _apply_handle_missing,
    "convert_type": _apply_convert_type,
    "normalize_categories": _apply_normalize_categories,
    "mask_email": _apply_mask_email,
    "mask_phone": _apply_mask_phone,
    "mask_identifier": _apply_mask_identifier,
}


def prepare_operation_config(operation: str, raw_config: dict[str, Any]) -> dict[str, Any]:
    definition = OPERATIONS.get(operation)
    if definition is None:
        raise CleaningValidationError("Choose a supported cleaning operation.")
    config = dict(raw_config or {})
    config["operation"] = operation
    if "columns" in config and config["columns"] is not None:
        columns = config["columns"]
        if not isinstance(columns, list):
            raise CleaningValidationError("Columns must be provided as a list.")
        config["columns"] = [str(column) for column in columns][:MAX_COLUMNS_PER_OPERATION]
    if not definition.accepts_columns:
        config.pop("columns", None)
    if operation == "mask_identifier":
        config.setdefault("salt", secrets.token_hex(16))
        prefix = str(config.get("prefix") or "ID-")[:12]
        if any(character in prefix for character in "\r\n\t"):
            raise CleaningValidationError("Pseudonym prefix contains invalid characters.")
        config["prefix"] = prefix
    if operation == "handle_missing":
        strategy = str(config.get("strategy", "")).lower()
        if strategy not in {"drop_rows", "constant", "mean", "median", "mode"}:
            raise CleaningValidationError("Choose a supported missing-value strategy.")
        config["strategy"] = strategy
    if operation == "convert_type":
        target_type = str(config.get("target_type", "")).lower()
        if target_type not in {"string", "integer", "float", "date", "boolean"}:
            raise CleaningValidationError("Choose a supported target type.")
        config["target_type"] = target_type
    return config


def apply_operation(frame: pd.DataFrame, config: dict[str, Any]) -> OperationResult:
    _require_frame(frame)
    operation = str(config.get("operation", ""))
    handler = HANDLERS.get(operation)
    if handler is None:
        raise CleaningValidationError("Choose a supported cleaning operation.")
    if frame.shape[1] == 0 or frame.shape[0] == 0:
        raise CleaningValidationError("This dataset version has no data to clean.")
    result = handler(frame, config)
    if result.frame.shape[1] == 0:
        raise CleaningValidationError("The operation would leave no columns.")
    return result
