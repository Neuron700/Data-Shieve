from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.datasets.models import AnalysisStatus, Dataset
from apps.profiling.models import AnalysisRun
from apps.profiling.services.parser import DatasetParseError
from apps.profiling.services.version_io import load_dataset_version

from .services.exporters import (
    build_cleaning_markdown,
    build_profiling_report,
    build_quality_report,
    json_bytes,
    quality_csv_bytes,
    to_csv_bytes,
    to_xlsx_bytes,
)


def _dataset(request, pk: int) -> Dataset:
    return get_object_or_404(
        Dataset.objects.select_related("project"), pk=pk, owner=request.user
    )


def _version(dataset: Dataset, request):
    versions = dataset.versions.all()
    version_id = request.GET.get("version")
    if version_id:
        return get_object_or_404(versions, pk=version_id)
    version = versions.order_by("-version_number").first()
    if not version:
        raise Http404("Dataset version not found.")
    return version


def _analysis_for_version(version):
    return (
        AnalysisRun.objects.filter(
            dataset_version=version,
            status=AnalysisStatus.COMPLETED,
        )
        .select_related("dataset_version", "dataset_version__dataset")
        .order_by("-started_at", "-pk")
        .first()
    )


def _attachment(data: bytes, filename: str, content_type: str) -> HttpResponse:
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def reports_page(request, pk: int):
    dataset = _dataset(request, pk)
    version = _version(dataset, request)
    analysis = _analysis_for_version(version)
    versions = dataset.versions.order_by("-version_number")
    return render(
        request,
        "reports/index.html",
        {
            "dataset": dataset,
            "version": version,
            "analysis": analysis,
            "versions": versions,
            "active_nav": "datasets",
        },
    )


@login_required
def export_version(request, pk: int, file_format: str):
    dataset = _dataset(request, pk)
    version = _version(dataset, request)
    if file_format not in {"csv", "xlsx"}:
        raise Http404("Unsupported export format.")
    try:
        parsed = load_dataset_version(version)
    except DatasetParseError as exc:
        messages.error(request, str(exc))
        return redirect("reports:index", pk=dataset.pk)
    filename = f"datasieve_dataset_{dataset.pk}_v{version.version_number}.{file_format}"
    if file_format == "csv":
        return _attachment(
            to_csv_bytes(parsed.frame),
            filename,
            "text/csv; charset=utf-8",
        )
    return _attachment(
        to_xlsx_bytes(parsed.frame, f"v{version.version_number}"),
        filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@login_required
def profiling_report_json(request, pk: int):
    dataset = _dataset(request, pk)
    version = _version(dataset, request)
    analysis = _analysis_for_version(version)
    if analysis is None:
        messages.error(request, "No completed analysis is available for this version.")
        return redirect("reports:index", pk=dataset.pk)
    payload = build_profiling_report(
        analysis,
        project_name=dataset.project.name,
        dataset_original_filename=dataset.original_filename,
    )
    return _attachment(
        json_bytes(payload),
        f"datasieve_profile_dataset_{dataset.pk}_v{version.version_number}.json",
        "application/json; charset=utf-8",
    )


@login_required
def quality_report(request, pk: int, file_format: str):
    dataset = _dataset(request, pk)
    version = _version(dataset, request)
    analysis = _analysis_for_version(version)
    if analysis is None:
        messages.error(request, "No completed analysis is available for this version.")
        return redirect("reports:index", pk=dataset.pk)
    if file_format not in {"json", "csv"}:
        raise Http404("Unsupported report format.")
    if file_format == "json":
        data = json_bytes(build_quality_report(analysis))
        content_type = "application/json; charset=utf-8"
    else:
        data = quality_csv_bytes(analysis)
        content_type = "text/csv; charset=utf-8"
    return _attachment(
        data,
        f"datasieve_quality_dataset_{dataset.pk}_v{version.version_number}.{file_format}",
        content_type,
    )


@login_required
def cleaning_summary_markdown(request, pk: int):
    dataset = _dataset(request, pk)
    markdown = build_cleaning_markdown(dataset)
    return _attachment(
        markdown.encode("utf-8"),
        f"datasieve_cleaning_summary_dataset_{dataset.pk}.md",
        "text/markdown; charset=utf-8",
    )
