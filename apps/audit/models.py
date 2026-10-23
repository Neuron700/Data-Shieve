from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    dataset = models.ForeignKey(
        "datasets.Dataset",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80)
    safe_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"], name="audit_owner_created_idx"),
            models.Index(fields=["dataset", "-created_at"], name="audit_dataset_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} · {self.created_at:%Y-%m-%d %H:%M}"
