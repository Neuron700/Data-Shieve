from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.datasets.models import AnalysisStatus, DatasetVersion


class AnalysisRun(models.Model):
    dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="analysis_runs",
    )
    status = models.CharField(
        max_length=20,
        choices=AnalysisStatus.choices,
        default=AnalysisStatus.PENDING,
    )
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]
        indexes = [
            models.Index(
                fields=["dataset_version", "-started_at"],
                name="analysis_version_started_idx",
            ),
            models.Index(fields=["status"], name="analysis_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Analysis {self.pk} · {self.dataset_version}"


class ColumnProfile(models.Model):
    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="column_profiles",
    )
    column_name = models.CharField(max_length=300)
    detected_type = models.CharField(max_length=30)
    non_null_count = models.PositiveIntegerField()
    null_count = models.PositiveIntegerField()
    unique_count = models.PositiveIntegerField()
    duplicate_value_count = models.PositiveIntegerField()
    summary_statistics = models.JSONField(default=dict, blank=True)
    inferred_patterns = models.JSONField(default=list, blank=True)
    quality_score = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis_run", "column_name"],
                name="unique_column_profile_per_run",
            ),
        ]
        indexes = [
            models.Index(fields=["analysis_run", "column_name"], name="profile_run_column_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.column_name} ({self.detected_type})"

    @property
    def missing_percentage(self) -> float:
        total = self.non_null_count + self.null_count
        return round((self.null_count / total) * 100, 1) if total else 0.0
