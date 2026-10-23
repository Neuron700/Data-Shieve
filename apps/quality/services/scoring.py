"""Transparent, deterministic quality scoring for DataSieve.

The formula is documented in docs/SCORING.md and stored with each analysis
summary so a score can always be traced to the findings that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.conf import settings

from apps.profiling.services.profiler import DatasetMetrics, ProfiledColumn

from .rules import IssueFinding, RULE_MAP

FORMULA_VERSION = "1.0"
SCORING_LIMITATIONS = [
    "The score is a product indicator, not an industry benchmark or certification.",
    "Rules check structural and heuristic patterns; they do not prove semantic correctness.",
    "Columns are assessed by affected-row share; business importance is not inferred.",
    "Potential outliers (DS008) are reported but do not reduce the score.",
    "Sensitive-data detection is heuristic and cannot find every form of personal data.",
]


@dataclass
class ScoreResult:
    score: int
    category: str
    contributions: list[dict]
    formula_version: str = FORMULA_VERSION
    limitations: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "category": self.category,
            "formula_version": self.formula_version,
            "contributions": self.contributions,
            "limitations": self.limitations or list(SCORING_LIMITATIONS),
        }


def score_category(score: int) -> str:
    bands = settings.QUALITY_SCORE_BANDS
    if score >= bands["strong"]:
        return "Strong"
    if score >= bands["good"]:
        return "Good"
    if score >= bands["needs_review"]:
        return "Needs Review"
    return "Poor"


def _structural_penalties(
    findings: Iterable[IssueFinding],
    metrics: DatasetMetrics,
) -> tuple[float, list[dict]]:
    weights = settings.QUALITY_SCORE_WEIGHTS
    rows = max(metrics.row_count, 1)
    per_rule: dict[str, float] = {}
    per_rule_counts: dict[str, int] = {}

    for finding in findings:
        if finding.rule_id in {"DS001", "DS002", "DS008"}:
            continue
        weight = float(weights.get(finding.rule_id, 0.0))
        if weight <= 0:
            continue
        if finding.rule_id == "DS003":
            share = min(1.0, finding.issue_count / max(metrics.column_count, 1))
            scaled = max(0.25, share)
        else:
            scaled = max(0.25, min(1.0, finding.issue_count / rows))
        per_rule[finding.rule_id] = per_rule.get(finding.rule_id, 0.0) + weight * scaled
        per_rule_counts[finding.rule_id] = per_rule_counts.get(finding.rule_id, 0) + 1

    total = min(float(weights["structural_max"]), sum(per_rule.values()))
    contributions = [
        {
            "key": rule_id,
            "label": RULE_MAP[rule_id].name if rule_id in RULE_MAP else rule_id,
            "points": round(points, 2),
            "finding_count": per_rule_counts[rule_id],
        }
        for rule_id, points in sorted(per_rule.items(), key=lambda item: item[1], reverse=True)
    ]
    return total, contributions


def compute_dataset_score(
    metrics: DatasetMetrics,
    findings: list[IssueFinding],
) -> ScoreResult:
    weights = settings.QUALITY_SCORE_WEIGHTS
    if metrics.total_cells <= 0:
        return ScoreResult(score=0, category="Needs Review", contributions=[])

    missing_ratio = metrics.missing_cells / metrics.total_cells
    duplicate_ratio = metrics.duplicate_rows / max(metrics.row_count, 1)
    missing_penalty = min(
        float(weights["missing_max"]),
        float(weights["missing_max"]) * 2 * missing_ratio,
    )
    duplicate_penalty = min(
        float(weights["duplicate_max"]),
        float(weights["duplicate_max"]) * 2 * duplicate_ratio,
    )
    structural_penalty, structural_contributions = _structural_penalties(findings, metrics)

    total_penalty = missing_penalty + duplicate_penalty + structural_penalty
    score = max(0, min(100, round(100 - total_penalty)))
    category = score_category(score)

    contributions: list[dict] = []
    if missing_penalty:
        contributions.append(
            {
                "key": "missing",
                "label": "Missing cells",
                "points": round(missing_penalty, 2),
                "finding_count": 1 if metrics.missing_cells else 0,
                "detail": f"{metrics.missing_cells:,} of {metrics.total_cells:,} cells",
            }
        )
    if duplicate_penalty:
        contributions.append(
            {
                "key": "duplicates",
                "label": "Duplicate rows",
                "points": round(duplicate_penalty, 2),
                "finding_count": 1 if metrics.duplicate_rows else 0,
                "detail": f"{metrics.duplicate_rows:,} duplicate row(s)",
            }
        )
    contributions.extend(structural_contributions)
    contributions.sort(key=lambda item: item["points"], reverse=True)

    return ScoreResult(
        score=score,
        category=category,
        contributions=contributions,
        limitations=list(SCORING_LIMITATIONS),
    )


COLUMN_RULE_WEIGHTS = {
    "DS004": 4.0,
    "DS005": 10.0,
    "DS006": 4.0,
    "DS007": 6.0,
    "DS009": 8.0,
    "DS010": 3.0,
}


def compute_column_scores(
    columns: list[ProfiledColumn],
    findings: list[IssueFinding],
    row_count: int,
) -> dict[str, int]:
    rows = max(row_count, 1)
    scores: dict[str, int] = {}
    by_column: dict[str, list[IssueFinding]] = {}
    for finding in findings:
        by_column.setdefault(finding.column_name, []).append(finding)

    for column in columns:
        null_penalty = min(45.0, (column.null_count / rows) * 80.0)
        finding_penalty = 0.0
        for finding in by_column.get(column.column_name, []):
            weight = COLUMN_RULE_WEIGHTS.get(finding.rule_id, 0.0)
            if weight <= 0:
                continue
            finding_penalty += weight * max(0.25, min(1.0, finding.issue_count / rows))
        score = max(0, min(100, round(100 - null_penalty - min(45.0, finding_penalty))))
        scores[column.column_name] = score
    return scores
