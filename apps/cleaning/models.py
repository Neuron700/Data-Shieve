from __future__ import annotations

from django.db import models

from apps.datasets.models import Dataset, DatasetVersion


class CleaningJob(models.Model):
    class Status(models.TextChoices):
        PREVIEWED = "previewed", "Previewed"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"
        DISCARDED = "discarded", "Discarded"

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="cleaning_jobs",
    )
    source_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.CASCADE,
        related_name="cleaning_jobs_from",
    )
    output_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cleaning_jobs_to",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PREVIEWED,
    )
    operation_config = models.JSONField(default=dict)
    preview_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["dataset", "-created_at"], name="job_dataset_created_idx"),
            models.Index(fields=["status"], name="job_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(output_version=models.F("source_version")),
                name="cleaning_output_differs_from_source",
            ),
        ]

    def __str__(self) -> str:
        return f"Cleaning job {self.pk} · {self.dataset}"
