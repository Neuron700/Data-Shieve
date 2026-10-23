from __future__ import annotations

from django.db import models

from apps.profiling.models import AnalysisRun


class QualityIssue(models.Model):
    class Severity(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        INFO = "info", "Info"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="quality_issues",
    )
    column_name = models.CharField(max_length=300, blank=True)
    rule_id = models.CharField(max_length=10)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    issue_type = models.CharField(max_length=120)
    issue_count = models.PositiveIntegerField()
    explanation = models.TextField()
    recommendation = models.TextField()
    sample_positions = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["rule_id", "column_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis_run", "rule_id", "column_name"],
                name="unique_issue_per_rule_column_run",
            ),
        ]
        indexes = [
            models.Index(fields=["analysis_run", "severity"], name="issue_run_severity_idx"),
            models.Index(fields=["analysis_run", "rule_id"], name="issue_run_rule_idx"),
            models.Index(fields=["analysis_run", "status"], name="issue_run_status_idx"),
            models.Index(fields=["column_name"], name="issue_column_idx"),
        ]

    def __str__(self) -> str:
        scope = self.column_name or "Dataset"
        return f"{self.rule_id} · {scope}"

    @property
    def severity_rank(self) -> int:
        return {
            self.Severity.HIGH: 1,
            self.Severity.MEDIUM: 2,
            self.Severity.LOW: 3,
            self.Severity.INFO: 4,
        }.get(self.severity, 5)

    @property
    def sample_rows(self) -> list[int]:
        if isinstance(self.sample_positions, list):
            return [int(item) for item in self.sample_positions[:20]]
        return []
