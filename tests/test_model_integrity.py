from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from apps.cleaning.models import CleaningJob
from apps.datasets.models import Dataset, DatasetVersion
from apps.projects.models import Project


def make_dataset(user: User, project: Project) -> tuple[Dataset, DatasetVersion]:
    dataset = Dataset.objects.create(
        project=project,
        owner=user,
        original_filename="orders.csv",
        stored_file_path=f"datasets/{user.pk}/orders.csv",
        file_format="csv",
        file_size=24,
        row_count=2,
        column_count=2,
        status=Dataset.Status.READY,
    )
    version = DatasetVersion.objects.create(
        dataset=dataset,
        version_number=1,
        file_path=dataset.stored_file_path,
        version_type=DatasetVersion.Type.ORIGINAL,
        row_count=2,
        column_count=2,
        processing_summary={
            "parse": {
                "file_format": "csv",
                "encoding": "utf-8-sig",
                "delimiter": ",",
                "original_headers": ["name", "amount"],
                "warnings": [],
            }
        },
    )
    return dataset, version


@pytest.mark.django_db
def test_deleting_project_removes_dataset_versions_and_cleaning_jobs() -> None:
    user = User.objects.create_user(username="owner", password="test-pass-123")
    project = Project.objects.create(owner=user, name="Orders")
    dataset, version = make_dataset(user, project)
    job = CleaningJob.objects.create(
        dataset=dataset,
        source_version=version,
        operation_config={"operation": "trim_whitespace"},
    )

    project.delete()

    assert not Project.objects.filter(pk=project.pk).exists()
    assert not Dataset.objects.filter(pk=dataset.pk).exists()
    assert not DatasetVersion.objects.filter(pk=version.pk).exists()
    assert not CleaningJob.objects.filter(pk=job.pk).exists()
