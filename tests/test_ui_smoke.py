from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook

from apps.cleaning.models import CleaningJob
from apps.datasets.models import AnalysisStatus, Dataset, DatasetVersion
from apps.datasets.views import PENDING_SESSION_KEY
from apps.projects.models import Project


CSV_CONTENT = (
    "name,email,phone,city,amount,notes\n"
    "Ana,ana@example.com,0812-3456-7890,Jakarta,10, first\n"
    "Budi,budi@example.com,0812-3456-7891,Bandung,20,second \n"
    "Ana,ana@example.com,0812-3456-7890,Jakarta,10, first\n"
    "Citra,,0812-3456-7892,Jakarta,, \n"
).encode("utf-8")


@pytest.fixture
def workspace(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="owner", email="owner@example.com", password="test-pass-123"
    )
    project = Project.objects.create(owner=user, name="Customer review")
    client.force_login(user)
    response = client.post(
        reverse("datasets:upload", args=[project.pk]),
        {
            "file": SimpleUploadedFile("customers.csv", CSV_CONTENT, content_type="text/csv"),
            "delimiter": "auto",
        },
    )
    assert response.status_code == 302
    dataset = Dataset.objects.get(owner=user)
    assert dataset.versions.count() == 1
    return {
        "client": client,
        "user": user,
        "project": project,
        "dataset": dataset,
        "version": dataset.versions.get(),
    }


@pytest.mark.django_db
def test_primary_pages_render(workspace):
    client = workspace["client"]
    dataset = workspace["dataset"]
    version = workspace["version"]
    project = workspace["project"]
    analysis = dataset.latest_analysis
    assert analysis is not None
    assert analysis.status == AnalysisStatus.COMPLETED
    profile = analysis.column_profiles.first()
    assert profile is not None

    urls = [
        reverse("projects:dashboard"),
        reverse("projects:list"),
        reverse("projects:detail", args=[project.pk]),
        reverse("projects:edit", args=[project.pk]),
        reverse("projects:delete", args=[project.pk]),
        reverse("datasets:list"),
        reverse("datasets:detail", args=[dataset.pk]),
        reverse("datasets:preview", args=[dataset.pk]),
        reverse("datasets:versions", args=[dataset.pk]),
        reverse("datasets:delete", args=[dataset.pk]),
        reverse("profiling:overview", args=[dataset.pk]),
        reverse("profiling:column_detail", args=[dataset.pk, profile.pk]),
        reverse("quality:findings", args=[dataset.pk]),
        reverse("quality:rules"),
        reverse("cleaning:workspace", args=[dataset.pk]),
        reverse("reports:index", args=[dataset.pk]),
        reverse("accounts:settings"),
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        assert b"DataSieve" in response.content, url

    assert client.get(f"{reverse('datasets:detail', args=[dataset.pk])}?version={version.pk}").status_code == 200


@pytest.mark.django_db
def test_preview_page_redacts_sensitive_values(workspace):
    client = workspace["client"]
    dataset = workspace["dataset"]

    response = client.get(reverse("datasets:preview", args=[dataset.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "ana@example.com" not in content
    assert "0812-3456-7890" not in content
    assert "Jakarta" in content


@pytest.mark.django_db
def test_cleaning_workspace_creates_and_renders_preview(workspace):
    client = workspace["client"]
    dataset = workspace["dataset"]
    version = workspace["version"]

    response = client.post(
        reverse("cleaning:workspace", args=[dataset.pk]),
        {
            "source_version": str(version.pk),
            "operation": "mask_email",
            "columns": ["email"],
        },
    )

    assert response.status_code == 302
    job = CleaningJob.objects.get(dataset=dataset)
    assert job.status == CleaningJob.Status.PREVIEWED

    response = client.get(reverse("cleaning:preview", args=[job.pk]))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Cleaning preview" in content
    assert "ana@example.com" not in content
    assert "a***@example.com" in content

    response = client.post(reverse("cleaning:apply", args=[job.pk]))
    assert response.status_code == 302
    job.refresh_from_db()
    assert job.status == CleaningJob.Status.APPLIED
    assert job.output_version is not None
    assert job.output_version.version_type == DatasetVersion.Type.CLEANED
    assert job.output_version.version_number == 2


@pytest.mark.django_db
def test_cleaning_discard_keeps_source_and_hides_salt(workspace):
    client = workspace["client"]
    dataset = workspace["dataset"]
    version = workspace["version"]

    response = client.post(
        reverse("cleaning:workspace", args=[dataset.pk]),
        {
            "source_version": str(version.pk),
            "operation": "mask_identifier",
            "columns": ["name"],
        },
    )

    assert response.status_code == 302
    job = CleaningJob.objects.get(dataset=dataset)
    assert job.operation_config["salt"]
    assert "salt" not in job.preview_summary["safe_config"]

    response = client.post(reverse("cleaning:discard", args=[job.pk]))

    assert response.status_code == 302
    job.refresh_from_db()
    assert job.status == CleaningJob.Status.DISCARDED
    assert job.output_version is None
    assert version.version_type == DatasetVersion.Type.ORIGINAL
    assert dataset.versions.count() == 1


@pytest.mark.django_db
def test_report_downloads(workspace):
    client = workspace["client"]
    dataset = workspace["dataset"]

    for url in [
        reverse("reports:export_version", args=[dataset.pk, "csv"]),
        reverse("reports:export_version", args=[dataset.pk, "xlsx"]),
        reverse("reports:profile_json", args=[dataset.pk]),
        reverse("reports:quality", args=[dataset.pk, "json"]),
        reverse("reports:quality", args=[dataset.pk, "csv"]),
        reverse("reports:cleaning_markdown", args=[dataset.pk]),
    ]:
        response = client.get(url)
        assert response.status_code == 200, url
        assert response["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_auth_pages_and_empty_workspace_render(client, django_user_model):
    for url in [
        reverse("projects:home"),
        reverse("accounts:login"),
        reverse("accounts:signup"),
    ]:
        response = client.get(url)
        assert response.status_code == 200, url

    user = django_user_model.objects.create_user(
        username="empty", email="empty@example.com", password="test-pass-123"
    )
    client.force_login(user)
    for url in [
        reverse("projects:dashboard"),
        reverse("projects:list"),
        reverse("datasets:list"),
        reverse("quality:rules"),
        reverse("accounts:settings"),
        reverse("accounts:password_change"),
    ]:
        response = client.get(url)
        assert response.status_code == 200, url


@pytest.mark.django_db
def test_multi_sheet_upload_requires_and_imports_selected_sheet(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="sheets", email="sheets@example.com", password="test-pass-123"
    )
    project = Project.objects.create(owner=user, name="Workbook import")
    client.force_login(user)

    workbook = Workbook()
    first = workbook.active
    first.title = "First"
    first.append(["name", "value"])
    first.append(["Alpha", 1])
    second = workbook.create_sheet("Second")
    second.append(["city", "amount"])
    second.append(["Bandung", 42])
    buffer = io.BytesIO()
    workbook.save(buffer)

    response = client.post(
        reverse("datasets:upload", args=[project.pk]),
        {
            "file": SimpleUploadedFile(
                "workbook.xlsx",
                buffer.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            "delimiter": "auto",
        },
    )

    assert response.status_code == 200
    assert b"Worksheet" in response.content
    assert b"Second" in response.content
    pending = client.session[PENDING_SESSION_KEY]

    response = client.post(
        reverse("datasets:upload", args=[project.pk]),
        {
            "token": pending["token"],
            "delimiter": "auto",
            "sheet_name": "Second",
        },
    )

    assert response.status_code == 302
    dataset = Dataset.objects.get(project=project)
    assert dataset.file_format == "xlsx"
    assert dataset.latest_version.processing_summary["parse"]["sheet_name"] == "Second"
    assert dataset.row_count == 1


@pytest.mark.django_db
def test_example_csv_produces_expected_findings(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="example", email="example@example.com", password="test-pass-123"
    )
    project = Project.objects.create(owner=user, name="Example review")
    client.force_login(user)
    sample = Path(settings.BASE_DIR) / "examples" / "sample_customers.csv"

    response = client.post(
        reverse("datasets:upload", args=[project.pk]),
        {
            "file": SimpleUploadedFile(
                "sample_customers.csv",
                sample.read_bytes(),
                content_type="text/csv",
            ),
            "delimiter": "auto",
        },
    )

    assert response.status_code == 302
    dataset = Dataset.objects.get(project=project)
    analysis = dataset.latest_analysis
    assert analysis is not None
    assert analysis.status == AnalysisStatus.COMPLETED
    rule_ids = set(analysis.quality_issues.values_list("rule_id", flat=True))
    assert {"DS001", "DS002", "DS004", "DS006", "DS007", "DS009", "DS010"} <= rule_ids


@pytest.mark.django_db
def test_failed_inspection_removes_temp_upload(workspace):
    client = workspace["client"]
    project = workspace["project"]
    temp_root = Path(settings.TEMP_UPLOAD_ROOT)
    before = set(temp_root.rglob("*.xlsx")) if temp_root.exists() else set()

    response = client.post(
        reverse("datasets:upload", args=[project.pk]),
        {
            "file": SimpleUploadedFile(
                "broken.xlsx",
                b"this is not a workbook",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            "delimiter": "auto",
        },
    )

    assert response.status_code == 200
    after = set(temp_root.rglob("*.xlsx")) if temp_root.exists() else set()
    assert after == before


@pytest.mark.django_db
def test_owner_isolation_and_error_page(workspace, client, django_user_model):
    other = django_user_model.objects.create_user(
        username="other", password="test-pass-123"
    )
    client.force_login(other)
    dataset = workspace["dataset"]

    assert client.get(reverse("datasets:detail", args=[dataset.pk])).status_code == 404
    assert client.get(reverse("cleaning:workspace", args=[dataset.pk])).status_code == 404

    response = client.get("/definitely-not-a-page/")
    assert response.status_code == 404
    assert b"This page is not here" in response.content
