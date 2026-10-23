from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.datasets.models import Dataset
from apps.profiling.models import AnalysisRun
from apps.profiling.services.parser import DatasetParseError
from apps.profiling.services.version_io import load_dataset_version

from .forms import CleaningForm
from .models import CleaningJob
from .services.engine import apply_cleaning_job, build_cleaning_preview
from .services.operations import (
    OPERATIONS,
    CleaningValidationError,
    prepare_operation_config,
)

logger = logging.getLogger(__name__)


def _dataset_for_user(request, pk: int) -> Dataset:
    return get_object_or_404(Dataset, pk=pk, owner=request.user)


def _columns_context(dataset: Dataset):
    version = dataset.versions.order_by("-version_number").first()
    if version is None:
        return [], []
    try:
        parsed = load_dataset_version(version)
    except DatasetParseError:
        return [], []
    profile_map = {}
    run = (
        AnalysisRun.objects.filter(
            dataset_version=version, status="completed"
        )
        .order_by("-started_at", "-pk")
        .first()
    )
    if run:
        profile_map = {
            profile.column_name: profile for profile in run.column_profiles.all()
        }
    columns = [
        {
            "name": str(column),
            "detected_type": getattr(profile_map.get(str(column)), "detected_type", ""),
            "null_count": getattr(profile_map.get(str(column)), "null_count", None),
        }
        for column in parsed.frame.columns
    ]
    return columns, []


def _parse_mapping(raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CleaningValidationError("Category mapping must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise CleaningValidationError("Category mapping must be a JSON object.")
    if len(parsed) > 500:
        raise CleaningValidationError("Category mapping is limited to 500 entries.")
    mapped: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise CleaningValidationError("Category mapping keys and values must be text.")
        if not key or not value:
            raise CleaningValidationError("Category mapping keys and values cannot be empty.")
        mapped[key] = value
    return mapped


def _build_config(request, form: CleaningForm) -> dict:
    operation = form.cleaned_data["operation"]
    definition = OPERATIONS.get(operation)
    if definition is None:
        raise CleaningValidationError("Choose a supported cleaning operation.")
    columns = request.POST.getlist("columns")
    config: dict = {"columns": columns}
    if operation == "handle_missing":
        config["strategy"] = form.cleaned_data.get("strategy") or ""
        config["constant_value"] = form.cleaned_data.get("constant_value") or ""
    elif operation == "convert_type":
        config["target_type"] = form.cleaned_data.get("target_type") or ""
    elif operation == "normalize_categories":
        config["mapping"] = _parse_mapping(form.cleaned_data.get("mapping_text") or "")
    elif operation == "mask_identifier":
        config["prefix"] = form.cleaned_data.get("identifier_prefix") or "ID-"
    return prepare_operation_config(operation, config)


def _clean_context(request, dataset: Dataset, form=None, selected_version=None):
    versions = list(dataset.versions.order_by("-version_number"))
    if selected_version is None:
        version_id = request.GET.get("version")
        if version_id:
            selected_version = next(
                (version for version in versions if str(version.pk) == version_id),
                versions[0] if versions else None,
            )
        else:
            selected_version = versions[0] if versions else None
    columns = []
    load_error = ""
    if selected_version is not None:
        try:
            parsed = load_dataset_version(selected_version)
            run = (
                AnalysisRun.objects.filter(
                    dataset_version=selected_version, status="completed"
                )
                .order_by("-started_at", "-pk")
                .first()
            )
            profile_map = (
                {
                    profile.column_name: profile
                    for profile in run.column_profiles.all()
                }
                if run
                else {}
            )
            columns = [
                {
                    "name": str(column),
                    "detected_type": getattr(profile_map.get(str(column)), "detected_type", ""),
                    "null_count": getattr(profile_map.get(str(column)), "null_count", None),
                }
                for column in parsed.frame.columns
            ]
        except DatasetParseError as exc:
            load_error = str(exc)
    if form is None:
        initial = {}
        if selected_version is not None:
            initial["source_version"] = str(selected_version.pk)
        form = CleaningForm(dataset, initial=initial)
    jobs = (
        CleaningJob.objects.filter(dataset=dataset)
        .select_related("source_version", "output_version")
        .order_by("-created_at")[:10]
    )
    return {
        "dataset": dataset,
        "form": form,
        "versions": versions,
        "selected_version": selected_version,
        "selected_columns": request.POST.getlist("columns") if request.method == "POST" else [],
        "selected_operation": form["operation"].value() or "trim_whitespace",
        "columns": columns,
        "load_error": load_error,
        "operations": list(OPERATIONS.values()),
        "jobs": jobs,
        "active_nav": "datasets",
    }


@login_required
def workspace(request, pk: int):
    dataset = _dataset_for_user(request, pk)
    if not dataset.versions.exists():
        messages.error(request, "This dataset has no versions to clean.")
        return redirect("datasets:detail", pk=dataset.pk)

    if request.method == "POST":
        form = CleaningForm(dataset, request.POST)
        if form.is_valid():
            try:
                version = get_object_or_404(
                    dataset.versions, pk=form.cleaned_data["source_version"]
                )
                config = _build_config(request, form)
                preview = build_cleaning_preview(version, config)
                job = CleaningJob.objects.create(
                    dataset=dataset,
                    source_version=version,
                    status=CleaningJob.Status.PREVIEWED,
                    operation_config=config,
                    preview_summary=preview,
                )
                AuditEvent.objects.create(
                    owner=request.user,
                    dataset=dataset,
                    action="cleaning.previewed",
                    safe_metadata={
                        "job_id": job.pk,
                        "operation": config.get("operation"),
                        "source_version": version.version_number,
                    },
                )
                if not preview["can_apply"]:
                    messages.warning(
                        request,
                        "Preview created, but this configuration cannot be applied as-is.",
                    )
                return redirect("cleaning:preview", pk=job.pk)
            except CleaningValidationError as exc:
                messages.error(request, str(exc))
            except DatasetParseError as exc:
                messages.error(request, str(exc))
            except Exception as exc:  # noqa: BLE001 - do not leak data in errors
                logger.warning("Cleaning preview failed type=%s", exc.__class__.__name__)
                messages.error(
                    request,
                    "The preview could not be generated safely. Check the configuration and try again.",
                )
        else:
            messages.error(request, "Please correct the highlighted fields.")
        selected_version_id = request.POST.get("source_version")
        selected_version = (
            dataset.versions.filter(pk=selected_version_id).first()
            if str(selected_version_id).isdigit()
            else None
        )
        return render(
            request,
            "cleaning/workspace.html",
            _clean_context(request, dataset, form, selected_version),
        )

    context = _clean_context(request, dataset)
    return render(request, "cleaning/workspace.html", context)


@login_required
def job_preview(request, pk: int):
    job = get_object_or_404(
        CleaningJob.objects.select_related(
            "dataset", "source_version", "output_version", "dataset__project"
        ),
        pk=pk,
        dataset__owner=request.user,
    )
    return render(
        request,
        "cleaning/preview.html",
        {
            "job": job,
            "dataset": job.dataset,
            "source_version": job.source_version,
            "preview": job.preview_summary or {},
            "active_nav": "datasets",
        },
    )


@login_required
def apply_job(request, pk: int):
    if request.method != "POST":
        return redirect("cleaning:preview", pk=pk)
    job = get_object_or_404(CleaningJob, pk=pk, dataset__owner=request.user)
    if job.status != CleaningJob.Status.PREVIEWED:
        messages.error(request, "This cleaning job is not awaiting confirmation.")
        return redirect("cleaning:preview", pk=pk)
    try:
        output_version, analysis_run_id = apply_cleaning_job(job, owner=request.user)
    except CleaningValidationError as exc:
        messages.error(request, str(exc))
        return redirect("cleaning:preview", pk=pk)
    except DatasetParseError as exc:
        messages.error(request, str(exc))
        return redirect("cleaning:preview", pk=pk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cleaning apply failed type=%s", exc.__class__.__name__)
        messages.error(
            request,
            "The cleaning operation could not be applied safely. The source dataset was not changed.",
        )
        return redirect("cleaning:preview", pk=pk)
    messages.success(
        request,
        f"Cleaned version v{output_version.version_number} created. The source version is unchanged.",
    )
    return redirect(
        f"{reverse('datasets:detail', args=[job.dataset_id])}?version={output_version.pk}"
    )


@login_required
def discard_job(request, pk: int):
    if request.method != "POST":
        return redirect("cleaning:preview", pk=pk)
    job = get_object_or_404(CleaningJob, pk=pk, dataset__owner=request.user)
    if job.status == CleaningJob.Status.PREVIEWED:
        job.status = CleaningJob.Status.DISCARDED
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at"])
        AuditEvent.objects.create(
            owner=request.user,
            dataset=job.dataset,
            action="cleaning.discarded",
            safe_metadata={"job_id": job.pk, "operation": job.operation_config.get("operation")},
        )
        messages.success(request, "Preview discarded. No version was created.")
    return redirect("cleaning:workspace", pk=job.dataset_id)
