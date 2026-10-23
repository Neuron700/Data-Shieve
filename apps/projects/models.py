from __future__ import annotations

from django.conf import settings
from django.db import models


class Project(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "-updated_at"], name="project_owner_updated_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="project_name_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return self.name
