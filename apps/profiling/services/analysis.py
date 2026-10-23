"""Orchestrates parsing, profiling, quality rules, scoring, and persistence."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.datasets.models import AnalysisStatus, Dataset, DatasetVersion
from apps.quality.models import QualityIssue
from apps.quality.services.rules import RULES, evaluate_rules
from apps.quality.services.scoring import (
    FORMULA_VERSION,
    compute_column_scores,
    compute_dataset_score,
)
from apps.profiling.models import AnalysisRun, ColumnProfile

from .parser import DatasetParseError
from .profiler import profile_frame
from .version_io import load_dataset_version

logger = logging.getLogger(__name__)


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, DatasetParseError):
        return str(exc)[:400]
    return (
        "Analysis could not be completed. The file may be malformed or could not be "
        "read safely. Try re-uploading it or contact support if the problem continues."
    )


def _rule_summary(findings) -> list[dict]:
    counts: dict[str, dict] = {}
    for rule in RULES:
        counts[rule.rule_id] = {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "description": rule.description,
            "severity": rule.default_severity,
            "finding_count": 0,
            "issue_count": 0,
        }
    for finding in findings:
        bucket = counts.setdefault(
            finding.rule_id,
            {
                "rule_id": finding.rule_id,
                "name": finding.rule_id,
                "description": "",
                "severity": finding.severity,
                "finding_count": 0,
                "issue_count": 0,
            },
        )
        bucket["finding_count"] += 1
        bucket["issue_count"] += finding.issue_count
    return list(counts.values())


def analyze_version(version: DatasetVersion) -> AnalysisRun:
    """Run analysis synchronously and persist all derived records.

    The source file is never modified. Failures are recorded on the run and do
    not raise out of the service, so the caller can keep the dataset available.
    """
    run = AnalysisRun.objects.create(
        dataset_version=version,
        status=AnalysisStatus.RUNNING,
    )
    dataset = version.dataset
    try:
        parsed = load_dataset_version(version)
        profile = profile_frame(parsed.frame)
        findings = evaluate_rules(
            parsed.frame,
            profile.metrics,
            profile,
            parsed.original_headers,
        )
        score = compute_dataset_score(profile.metrics, findings)
        column_scores = compute_column_scores(profile.columns, findings, profile.metrics.row_count)

        with transaction.atomic():
            profiles = [
                ColumnProfile(
                    analysis_run=run,
                    column_name=column.column_name,
                    detected_type=column.inference.detected_type,
                    non_null_count=column.non_null_count,
                    null_count=column.null_count,
                    unique_count=column.unique_count,
                    duplicate_value_count=column.duplicate_value_count,
                    summary_statistics=column.summary_statistics,
                    inferred_patterns=column.inferred_patterns,
                    quality_score=column_scores.get(column.column_name),
                )
                for column in profile.columns
            ]
            ColumnProfile.objects.bulk_create(profiles)

            issues = [
                QualityIssue(
                    analysis_run=run,
                    column_name=finding.column_name[:300],
                    rule_id=finding.rule_id,
                    severity=finding.severity,
                    issue_type=finding.issue_type[:120],
                    issue_count=finding.issue_count,
                    explanation=finding.explanation,
                    recommendation=finding.recommendation,
                    sample_positions=finding.sample_positions[:20],
                )
                for finding in findings
            ]
            QualityIssue.objects.bulk_create(issues)

            summary = {
                "engine_version": "1.0",
                "ruleset": [rule.rule_id for rule in RULES],
                "formula_version": FORMULA_VERSION,
                "dataset_metrics": profile.metrics.as_dict(),
                "score": score.as_dict(),
                "rule_summary": _rule_summary(findings),
                "parse": parsed.parse_metadata,
                "analyzed_at": timezone.now().isoformat(),
                "duration_seconds": round(
                    (timezone.now() - run.started_at).total_seconds(), 2
                ),
                "file_size": dataset.file_size,
            }
            run.summary = summary
            run.status = AnalysisStatus.COMPLETED
            run.completed_at = timezone.now()
            run.save(
                update_fields=["summary", "status", "completed_at"]
            )
            Dataset.objects.filter(pk=dataset.pk).update(status=Dataset.Status.READY)
        return run
    except Exception as exc:  # noqa: BLE001 - persist a safe failure state
        logger.warning(
            "Analysis failed for version_id=%s error_type=%s",
            version.pk,
            exc.__class__.__name__,
        )
        run.status = AnalysisStatus.FAILED
        run.error_message = _safe_error_message(exc)
        run.completed_at = timezone.now()
        run.summary = {
            "engine_version": "1.0",
            "failed": True,
            "error_code": exc.__class__.__name__,
        }
        run.save(update_fields=["status", "error_message", "completed_at", "summary"])
        Dataset.objects.filter(pk=dataset.pk).update(status=Dataset.Status.READY)
        return run


def analyze_version_safely(version: DatasetVersion) -> AnalysisRun:
    """Wrapper used by request flows that must never leak tracebacks."""
    try:
        return analyze_version(version)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Analysis wrapper failure type=%s", exc.__class__.__name__)
        return AnalysisRun.objects.filter(
            dataset_version=version, status=AnalysisStatus.FAILED
        ).order_by("-pk").first() or AnalysisRun.objects.create(
            dataset_version=version,
            status=AnalysisStatus.FAILED,
            completed_at=timezone.now(),
            error_message="Analysis could not be started safely.",
        )


def stale_analysis_cutoff(seconds: int = 3600):
    return timezone.now() - timedelta(seconds=seconds)
