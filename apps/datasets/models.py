from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone


class Dataset(models.Model):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        ANALYZING = "analyzing", "Analyzing"
        FAILED = "failed", "Failed"

    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        XLSX = "xlsx", "XLSX"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="datasets",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="datasets",
    )
    original_filename = models.CharField(max_length=255)
    stored_file_path = models.CharField(max_length=500)
    file_format = models.CharField(max_length=10, choices=Format.choices)
    file_size = models.PositiveBigIntegerField()
    row_count = models.PositiveIntegerField(default=0)
    column_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.READY)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"], name="dataset_owner_created_idx"),
            models.Index(fields=["project", "status"], name="dataset_project_status_idx"),
        ]

    def __str__(self) -> str:
        return self.original_filename

    @property
    def latest_version(self):
        return self.versions.order_by("-version_number").first()

    @property
    def original_version(self):
        return self.versions.filter(version_type=DatasetVersion.Type.ORIGINAL).first()

    @property
    def latest_analysis(self):
        return (
            self.analysis_runs.filter(status=AnalysisStatus.COMPLETED)
            .order_by("-started_at", "-pk")
            .first()
        )

    @property
    def analysis_runs(self):
        from apps.profiling.models import AnalysisRun

        return AnalysisRun.objects.filter(dataset_version__dataset=self)

    def file_path(self, path: str | None = None) -> Path:
        relative = path or self.stored_file_path
        return Path(settings.PRIVATE_MEDIA_ROOT) / relative


class DatasetVersion(models.Model):
    class Type(models.TextChoices):
        ORIGINAL = "original", "Original"
        CLEANED = "cleaned", "Cleaned"

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField()
    file_path = models.CharField(max_length=500)
    version_type = models.CharField(max_length=12, choices=Type.choices)
    row_count = models.PositiveIntegerField()
    column_count = models.PositiveIntegerField()
    parent_version = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    processing_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "version_number"],
                name="unique_dataset_version_number",
            ),
            models.CheckConstraint(
                condition=models.Q(version_number__gte=1),
                name="dataset_version_number_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["dataset", "-version_number"], name="version_dataset_number_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.dataset.original_filename} v{self.version_number}"

    @property
    def path(self) -> Path:
        return Path(settings.PRIVATE_MEDIA_ROOT) / self.file_path

    @property
    def label(self) -> str:
        return f"v{self.version_number} · {self.get_version_type_display()}"

    @property
    def latest_completed_analysis(self):
        return self.analysis_runs.filter(status=AnalysisStatus.COMPLETED).order_by(
            "-started_at", "-pk"
        ).first()


class AnalysisStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
