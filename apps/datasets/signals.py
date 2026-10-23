from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import DatasetVersion
from .services.storage import delete_private_file


@receiver(post_delete, sender=DatasetVersion, dispatch_uid="delete_dataset_version_file")
def delete_dataset_version_file(sender, instance: DatasetVersion, **kwargs) -> None:
    path = instance.file_path
    transaction.on_commit(lambda: delete_private_file(path))
