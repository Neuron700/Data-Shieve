from __future__ import annotations

import secrets
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.cleaning.models import CleaningJob
from apps.profiling.models import AnalysisRun
from apps.profiling.services.analysis import analyze_version
from apps.profiling.services.parser import (
    CSV_DELIMITERS,
    DatasetParseError,
    SheetSelectionRequired,
    inspect_upload,
    read_preview,
    sanitize_original_filename,
)
from apps.profiling.services.version_io import load_dataset_version
from apps.projects.models import Project
from apps.quality.models import QualityIssue

from .forms import ConfirmUploadForm, UploadDatasetForm
from .models import AnalysisStatus, Dataset, DatasetVersion
from .services.imports import ImportError, import_temp_dataset
from .services.privacy import redact_preview
from .services.queries import latest_completed_analysis_for_dataset
from .services.storage import (
    cleanup_stale_temp_uploads,
    delete_temp_upload,
    resolve_private_path,
    save_temp_upload,
)

PENDING_SESSION_KEY = "datasieve_pending_upload"


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _get_dataset(request, pk: int) -> Dataset:
    return get_object_or_404(
        Dataset.objects.select_related("project"), pk=pk, owner=request.user
    )


def _get_version(dataset: Dataset, version_id: str | None) -> DatasetVersion:
    versions = dataset.versions.all()
    if version_id:
        return get_object_or_404(versions, pk=version_id)
    version = versions.order_by("-version_number").first()
    if version is None:
        raise Dataset.DoesNotExist("This dataset has no versions.")
    return version


def _latest_runs(version: DatasetVersion) -> tuple[AnalysisRun | None, AnalysisRun | None]:
    completed = (
        version.analysis_runs.filter(status=AnalysisStatus.COMPLETED)
        .order_by("-started_at", "-pk")
        .first()
    )
    failed = (
        version.analysis_runs.filter(status=AnalysisStatus.FAILED)
        .order_by("-started_at", "-pk")
        .first()
    )
    return completed, failed


def _pending(request) -> dict | None:
    pending = request.session.get(PENDING_SESSION_KEY)
    if not isinstance(pending, dict):
        return None
    return pending


def _clear_pending(request) -> None:
    pending = _pending(request)
    if pending:
        delete_temp_upload(pending.get("temp_path"))
    request.session.pop(PENDING_SESSION_KEY, None)


def _save_pending(request, pending: dict) -> None:
    request.session[PENDING_SESSION_KEY] = pending


def _pending_expired(pending: dict) -> bool:
    created_at = pending.get("created_at")
    if not isinstance(created_at, (int, float)):
        return True
    return time.time() - created_at > settings.TEMP_UPLOAD_MAX_AGE_SECONDS


def _confirm_form(pending: dict, initial: dict | None = None) -> ConfirmUploadForm:
    form = ConfirmUploadForm(initial=initial)
    sheet_names = pending.get("sheet_names") or []
    form.fields["sheet_name"].choices = [("", "Choose a sheet")] + [
        (name, name) for name in sheet_names
    ]
    return form


def _confirm_context(request, pending: dict, form: ConfirmUploadForm | None = None):
    return render(
        request,
        "datasets/upload_confirm.html",
        {
            "form": form or _confirm_form(pending, {"token": pending["token"]}),
            "pending": pending,
            "sheet_names": pending.get("sheet_names", []),
            "delimiter_choices": [
                ("auto", "Auto-detect"),
                (",", "Comma (,)"),
                (";", "Semicolon (;)"),
                ("\t", "Tab"),
                ("|", "Pipe (|)"),
            ],
            "active_nav": "projects",
        },
    )


def _finish_import(request, pending: dict, delimiter: str | None, sheet_name: str | None):
    project = get_object_or_404(
        Project, pk=pending["project_id"], owner=request.user
    )
    try:
        result = import_temp_dataset(
            temp_relative_path=pending["temp_path"],
            original_filename=pending["original_filename"],
            file_format=pending["file_format"],
            project=project,
            owner=request.user,
            delimiter=delimiter,
            encoding=pending.get("encoding"),
            sheet_name=sheet_name,
        )
    except SheetSelectionRequired as exc:
        pending["sheet_names"] = exc.sheet_names
        _save_pending(request, pending)
        messages.error(request, "Choose a sheet before importing this workbook.")
        return _confirm_context(
            request,
            pending,
            _confirm_form(
                pending,
                {
                    "token": pending["token"],
                    "delimiter": delimiter or "auto",
                    "sheet_name": "",
                },
            ),
        )
    except (DatasetParseError, ImportError) as exc:
        messages.error(request, str(exc))
        return _confirm_context(
            request,
            pending,
            _confirm_form(
                pending,
                {
                    "token": pending["token"],
                    "delimiter": delimiter or "auto",
                    "sheet_name": sheet_name or "",
                },
            ),
        )

    request.session.pop(PENDING_SESSION_KEY, None)
    analysis = result.analysis_run
    if analysis.status == AnalysisStatus.COMPLETED:
        messages.success(
            request,
            "Dataset imported and profiled. Review the quality findings below.",
        )
    else:
        messages.warning(
            request,
            "Dataset imported, but analysis failed. You can retry analysis from the dataset page.",
        )
    return redirect("datasets:detail", pk=result.dataset.pk)


@login_required
def upload(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    cleanup_stale_temp_uploads()

    if request.method == "POST" and request.POST.get("token"):
        pending = _pending(request)
        form = ConfirmUploadForm(request.POST)
        if pending:
            sheet_names = pending.get("sheet_names") or []
            form.fields["sheet_name"].choices = [("", "Choose a sheet")] + [
                (name, name) for name in sheet_names
            ]
        if not pending or not form.is_valid():
            messages.error(request, "The upload session expired. Upload the file again.")
            return redirect("datasets:upload", project_id=project.pk)
        if not secrets.compare_digest(str(pending.get("token", "")), form.cleaned_data["token"]):
            messages.error(request, "The upload session is invalid. Upload the file again.")
            return redirect("datasets:upload", project_id=project.pk)
        if pending.get("project_id") != project.pk or _pending_expired(pending):
            delete_temp_upload(pending.get("temp_path"))
            request.session.pop(PENDING_SESSION_KEY, None)
            messages.error(request, "The upload session expired. Upload the file again.")
            return redirect("datasets:upload", project_id=project.pk)

        selected_delimiter = form.cleaned_data.get("delimiter") or "auto"
        selected_sheet = form.cleaned_data.get("sheet_name") or ""

        if pending["file_format"] == "csv":
            if selected_delimiter == "auto":
                if pending.get("delimiter_uncertain"):
                    messages.error(request, "Choose a CSV delimiter manually.")
                    return _confirm_context(request, pending, form)
                delimiter = pending.get("delimiter") or ","
            else:
                if selected_delimiter not in CSV_DELIMITERS:
                    messages.error(request, "Unsupported CSV delimiter.")
                    return _confirm_context(request, pending, form)
                delimiter = selected_delimiter
            return _finish_import(request, pending, delimiter, None)

        sheet_names = pending.get("sheet_names") or []
        if len(sheet_names) > 1 and selected_sheet not in sheet_names:
            messages.error(request, "Choose one of the sheets in this workbook.")
            return _confirm_context(request, pending, form)
        sheet_name = selected_sheet or (sheet_names[0] if sheet_names else None)
        return _finish_import(request, pending, None, sheet_name)

    if request.method == "POST":
        form = UploadDatasetForm(request.POST, request.FILES)
        if form.is_valid():
            upload_file = form.cleaned_data["file"]
            original_filename = sanitize_original_filename(upload_file.name)
            file_format = Path(original_filename).suffix.lower().lstrip(".")
            pending = _pending(request)
            if pending:
                delete_temp_upload(pending.get("temp_path"))
            temp_relative = None
            try:
                temp_relative, size = save_temp_upload(
                    upload_file, request.user.id, file_format
                )
                temp_path = resolve_private_path(temp_relative)
                inspection = inspect_upload(temp_path, file_format)
            except (DatasetParseError, OSError, ValueError) as exc:
                delete_temp_upload(temp_relative)
                messages.error(request, str(exc))
                return render(
                    request,
                    "datasets/upload.html",
                    {
                        "form": form,
                        "project": project,
                        "max_upload_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024),
                        "active_nav": "projects",
                    },
                )

            selected_delimiter = form.cleaned_data.get("delimiter") or "auto"
            needs_confirmation = (
                file_format == "xlsx" and len(inspection.sheet_names) > 1
            ) or (
                file_format == "csv"
                and selected_delimiter == "auto"
                and inspection.delimiter_uncertain
            )
            pending_data = {
                "token": secrets.token_urlsafe(24),
                "temp_path": temp_relative,
                "file_format": file_format,
                "original_filename": original_filename,
                "size": size,
                "encoding": inspection.encoding,
                "delimiter": inspection.delimiter,
                "delimiter_uncertain": inspection.delimiter_uncertain,
                "sheet_names": inspection.sheet_names,
                "project_id": project.pk,
                "created_at": time.time(),
            }
            _save_pending(request, pending_data)

            if needs_confirmation:
                messages.info(
                    request,
                    "One more choice is needed before this file can be imported.",
                )
                return _confirm_context(
                    request,
                    pending_data,
                    _confirm_form(
                        pending_data,
                        {
                            "token": pending_data["token"],
                            "delimiter": selected_delimiter,
                            "sheet_name": "",
                        },
                    ),
                )

            if file_format == "csv":
                delimiter = (
                    selected_delimiter
                    if selected_delimiter != "auto"
                    else inspection.delimiter
                )
                return _finish_import(request, pending_data, delimiter, None)
            sheet_name = inspection.sheet_names[0] if inspection.sheet_names else None
            return _finish_import(request, pending_data, None, sheet_name)
    else:
        form = UploadDatasetForm()

    return render(
        request,
        "datasets/upload.html",
        {
            "form": form,
            "project": project,
            "max_upload_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024),
            "active_nav": "projects",
        },
    )


@login_required
def dataset_list(request):
    datasets = (
        Dataset.objects.filter(owner=request.user)
        .select_related("project")
        .order_by("-created_at")
    )
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    project_id = request.GET.get("project") or ""
    date_from = _parse_date(request.GET.get("date_from"))
    date_to = _parse_date(request.GET.get("date_to"))
    if query:
        datasets = datasets.filter(original_filename__icontains=query)
    if status in dict(Dataset.Status.choices):
        datasets = datasets.filter(status=status)
    if project_id.isdigit():
        datasets = datasets.filter(project_id=int(project_id), project__owner=request.user)
    if date_from:
        datasets = datasets.filter(created_at__date__gte=date_from)
    if date_to:
        datasets = datasets.filter(created_at__date__lte=date_to)
    paginator = Paginator(datasets, 12)
    page = paginator.get_page(request.GET.get("page"))
    page_datasets = list(page.object_list)
    from .services.queries import latest_completed_analysis_map

    analysis_map = latest_completed_analysis_map(page_datasets)
    for dataset in page_datasets:
        dataset.latest_analysis_for_card = analysis_map.get(dataset.pk)
    projects = Project.objects.filter(owner=request.user).order_by("name")
    context = {
        "page": page,
        "query": query,
        "status": status,
        "project_id": project_id,
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "status_choices": Dataset.Status.choices,
        "projects": projects,
        "active_nav": "datasets",
    }
    return render(request, "datasets/list.html", context)


@login_required
def dataset_detail(request, pk: int):
    dataset = _get_dataset(request, pk)
    version = _get_version(dataset, request.GET.get("version"))
    completed_run, failed_run = _latest_runs(version)
    versions = list(
        dataset.versions.select_related("parent_version").order_by("-version_number")
    )
    jobs = list(
        CleaningJob.objects.filter(dataset=dataset)
        .select_related("source_version", "output_version")
        .order_by("-created_at")[:8]
    )
    preview = None
    preview_error = None
    column_map = {}
    if completed_run:
        column_map = {
            profile.column_name: profile for profile in completed_run.column_profiles.all()
        }
    try:
        parsed = load_dataset_version(version)
        preview = redact_preview(read_preview(parsed, limit=15))
    except DatasetParseError as exc:
        preview_error = str(exc)

    issue_counts = {}
    if completed_run:
        issue_counts = {
            row["severity"]: row["count"]
            for row in completed_run.quality_issues.values("severity").annotate(
                count=Count("id")
            )
        }
    issue_total = sum(issue_counts.values())
    return render(
        request,
        "datasets/detail.html",
        {
            "dataset": dataset,
            "version": version,
            "versions": versions,
            "completed_run": completed_run,
            "failed_run": failed_run,
            "score": (completed_run.summary or {}).get("score") if completed_run else None,
            "metrics": (completed_run.summary or {}).get("dataset_metrics")
            if completed_run
            else None,
            "score_contributions": (completed_run.summary or {}).get("score", {}).get(
                "contributions", []
            )
            if completed_run
            else [],
            "preview": preview,
            "preview_error": preview_error,
            "column_map": column_map,
            "jobs": jobs,
            "issue_counts": issue_counts,
            "issue_total": issue_total,
            "active_nav": "datasets",
        },
    )


@login_required
def dataset_preview(request, pk: int):
    dataset = _get_dataset(request, pk)
    version = _get_version(dataset, request.GET.get("version"))
    completed_run, failed_run = _latest_runs(version)
    preview = None
    preview_error = None
    try:
        parsed = load_dataset_version(version)
        preview = redact_preview(read_preview(parsed, limit=settings.PREVIEW_ROW_LIMIT))
    except DatasetParseError as exc:
        preview_error = str(exc)
    profile_map = {}
    if completed_run:
        profile_map = {
            profile.column_name: profile for profile in completed_run.column_profiles.all()
        }
    return render(
        request,
        "datasets/preview.html",
        {
            "dataset": dataset,
            "version": version,
            "completed_run": completed_run,
            "failed_run": failed_run,
            "preview": preview,
            "preview_error": preview_error,
            "profile_map": profile_map,
            "versions": dataset.versions.all(),
            "active_nav": "datasets",
        },
    )


@login_required
def analyze(request, pk: int):
    dataset = _get_dataset(request, pk)
    if request.method != "POST":
        return redirect("datasets:detail", pk=dataset.pk)
    version = _get_version(dataset, request.POST.get("version_id"))
    Dataset.objects.filter(pk=dataset.pk).update(status=Dataset.Status.ANALYZING)
    run = analyze_version(version)
    if run.status == AnalysisStatus.COMPLETED:
        score = (run.summary or {}).get("score", {}).get("score")
        AuditEvent.objects.create(
            owner=request.user,
            dataset=dataset,
            action="analysis.completed",
            safe_metadata={
                "analysis_id": run.pk,
                "version_number": version.version_number,
                "score": score,
            },
        )
        messages.success(request, "Profile and quality analysis completed.")
    else:
        AuditEvent.objects.create(
            owner=request.user,
            dataset=dataset,
            action="analysis.failed",
            safe_metadata={
                "analysis_id": run.pk,
                "version_number": version.version_number,
            },
        )
        messages.error(request, run.error_message or "Analysis failed.")
    return redirect(
        f"{reverse('datasets:detail', args=[dataset.pk])}?version={version.pk}"
    )


@login_required
def dataset_versions(request, pk: int):
    dataset = _get_dataset(request, pk)
    versions = list(
        dataset.versions.select_related("parent_version").order_by("-version_number")
    )
    version_ids = [version.pk for version in versions]
    runs = (
        AnalysisRun.objects.filter(
            dataset_version_id__in=version_ids,
        )
        .select_related("dataset_version")
        .order_by("-started_at", "-pk")
    )
    run_map: dict[int, AnalysisRun] = {}
    for run in runs:
        run_map.setdefault(run.dataset_version_id, run)
    jobs = list(
        CleaningJob.objects.filter(dataset=dataset)
        .select_related("source_version", "output_version")
        .order_by("-created_at")
    )
    for version in versions:
        version.latest_run_for_display = run_map.get(version.pk)
    return render(
        request,
        "datasets/versions.html",
        {
            "dataset": dataset,
            "versions": versions,
            "jobs": jobs,
            "active_nav": "datasets",
        },
    )


@login_required
def dataset_delete(request, pk: int):
    dataset = _get_dataset(request, pk)
    if request.method == "POST":
        dataset_id = dataset.pk
        project_id = dataset.project_id
        with transaction.atomic():
            CleaningJob.objects.filter(dataset=dataset).delete()
            dataset.delete()
        AuditEvent.objects.create(
            owner=request.user,
            dataset=None,
            action="dataset.deleted",
            safe_metadata={"dataset_id": dataset_id, "project_id": project_id},
        )
        messages.success(request, "Dataset, versions, and private files were deleted.")
        return redirect("datasets:list")
    return render(
        request,
        "datasets/delete.html",
        {
            "dataset": dataset,
            "version_count": dataset.versions.count(),
            "active_nav": "datasets",
        },
    )
