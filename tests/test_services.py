from __future__ import annotations

import io

import pandas as pd
import pytest
from openpyxl import load_workbook

from apps.cleaning.services.operations import (
    CleaningValidationError,
    apply_operation,
    prepare_operation_config,
)
from apps.profiling.services.profiler import DatasetMetrics, compute_dataset_metrics, profile_frame
from apps.quality.services.rules import evaluate_rules
from apps.quality.services.scoring import compute_dataset_score
from apps.reports.services.exporters import to_csv_bytes, to_xlsx_bytes


def metrics(**overrides) -> DatasetMetrics:
    values = {
        "row_count": 10,
        "column_count": 2,
        "total_cells": 20,
        "missing_cells": 0,
        "missing_percentage": 0.0,
        "duplicate_rows": 0,
        "duplicate_percentage": 0.0,
        "empty_rows": 0,
    }
    values.update(overrides)
    return DatasetMetrics(**values)


def test_score_uses_documented_missing_and_duplicate_penalties() -> None:
    result = compute_dataset_score(
        metrics(missing_cells=2, missing_percentage=10.0, duplicate_rows=1, duplicate_percentage=10.0),
        [],
    )

    assert result.score == 91
    assert result.category == "Strong"
    assert {item["key"] for item in result.contributions} == {"missing", "duplicates"}
    assert result.formula_version == "1.0"


def test_trim_whitespace_does_not_mutate_input() -> None:
    frame = pd.DataFrame({"city": [" Jakarta ", "Bandung"]})

    result = apply_operation(
        frame,
        {"operation": "trim_whitespace", "columns": ["city"]},
    )

    assert result.frame["city"].tolist() == ["Jakarta", "Bandung"]
    assert frame["city"].tolist() == [" Jakarta ", "Bandung"]


def test_conversion_rejects_invalid_values_without_partial_changes() -> None:
    frame = pd.DataFrame({"amount": ["10", "not-a-number"]})

    with pytest.raises(CleaningValidationError, match="cannot be converted"):
        apply_operation(
            frame,
            {"operation": "convert_type", "columns": ["amount"], "target_type": "integer"},
        )

    assert frame["amount"].tolist() == ["10", "not-a-number"]


def test_pseudonymization_is_stable_and_salt_is_generated() -> None:
    frame = pd.DataFrame({"customer_id": ["A-1", "A-2", "A-1"]})
    config = prepare_operation_config(
        "mask_identifier",
        {"columns": ["customer_id"], "prefix": "CUS-"},
    )

    result = apply_operation(frame, config)

    assert config["salt"]
    assert result.frame["customer_id"].nunique() == 2
    assert result.frame["customer_id"].iloc[0] == result.frame["customer_id"].iloc[2]
    assert result.frame["customer_id"].iloc[0].startswith("CUS-")
    assert "A-1" not in result.frame["customer_id"].tolist()


def test_sensitive_findings_do_not_copy_matched_values() -> None:
    frame = pd.DataFrame(
        {
            "email": ["ana@example.com", "budi@example.com"],
            "city": ["Jakarta", "Jakarta"],
        }
    )
    measured = compute_dataset_metrics(frame)
    profile = profile_frame(frame)

    findings = evaluate_rules(frame, measured, profile, list(frame.columns))
    email_findings = [finding for finding in findings if finding.rule_id == "DS009"]

    assert email_findings
    for finding in email_findings:
        assert "ana@example.com" not in finding.explanation
        assert "budi@example.com" not in finding.explanation


def test_formula_leading_values_are_neutralized_in_csv_and_xlsx() -> None:
    frame = pd.DataFrame({"name": ["=SUM(A1:A2)", "+cmd", "-1", "@user", "safe"]})

    csv_text = to_csv_bytes(frame).decode("utf-8-sig")
    assert "'=SUM(A1:A2)" in csv_text
    assert "'+cmd" in csv_text
    assert "'-1" in csv_text
    assert "'@user" in csv_text

    workbook = load_workbook(io.BytesIO(to_xlsx_bytes(frame)))
    sheet = workbook.active
    assert sheet["A2"].value == "=SUM(A1:A2)"
    assert sheet["A2"].data_type == "s"
    assert sheet["A3"].data_type == "s"
