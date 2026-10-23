from __future__ import annotations

from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.datasets.models import AnalysisStatus, Dataset, DatasetVersion
from apps.quality.models import QualityIssue

from .models import AnalysisRun, ColumnProfile


def _owned_dataset(request, pk: int) -> Dataset:
    return get_object_or_404(Dataset, pk=pk, owner=request.user)


def _version_for_request(dataset: Dataset, request) -> DatasetVersion:
    versions = dataset.versions.all()
    version_id = request.GET.get("version")
    if version_id:
        return get_object_or_404(versions, pk=version_id)
    version = versions.order_by("-version_number").first()
    if not version:
        raise Http404("Dataset version not found.")
    return version


def _run_for_request(dataset: Dataset, version: DatasetVersion, request):
    analysis_id = request.GET.get("analysis")
    if analysis_id:
        run = get_object_or_404(
            AnalysisRun.objects.select_related("dataset_version"),
            pk=analysis_id,
            dataset_version__dataset=dataset,
        )
        return run
    return (
        version.analysis_runs.filter(
            dataset_version__dataset=dataset,
        )
        .select_related("dataset_version")
        .order_by("-started_at", "-pk")
        .first()
    )


@login_required
def overview(request, pk: int):
    dataset = _owned_dataset(request, pk)
    version = _version_for_request(dataset, request)
    run = _run_for_request(dataset, version, request)
    if run and run.dataset_version_id != version.pk:
        version = run.dataset_version

    if run is None:
        messages.info(request, "Run analysis before viewing the profile overview.")
        return redirect("datasets:detail", pk=dataset.pk)

    columns = list(run.column_profiles.all()) if run.status == AnalysisStatus.COMPLETED else []
    issue_counts = {
        row["severity"]: row["count"]
        for row in run.quality_issues.values("severity").annotate(count=Count("id"))
    }
    column_issue_counts = {
        row["column_name"]: row["count"]
        for row in run.quality_issues.exclude(column_name="")
        .values("column_name")
        .annotate(count=Count("id"))
    }
    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    top_issues = sorted(
        run.quality_issues.all(),
        key=lambda issue: (
            severity_order.get(issue.severity, 5),
            -issue.issue_count,
            issue.rule_id,
        ),
    )[:6]
    summary = run.summary or {}
    metrics = summary.get("dataset_metrics", {})
    score = summary.get("score", {})
    chart_columns = sorted(
        columns,
        key=lambda column: column.null_count,
        reverse=True,
    )[:30]
    score_chart = {
        "labels": [column.column_name for column in columns[:30]],
        "values": [column.quality_score for column in columns[:30]],
    }
    versions = dataset.versions.order_by("-version_number")
    analysis_history = (
        AnalysisRun.objects.filter(dataset_version__dataset=dataset)
        .select_related("dataset_version")
        .order_by("-started_at", "-pk")[:8]
    )
    return render(
        request,
        "profiling/overview.html",
        {
            "dataset": dataset,
            "version": version,
            "run": run,
            "columns": columns,
            "issue_counts": issue_counts,
            "issue_total": sum(issue_counts.values()),
            "column_issue_counts": column_issue_counts,
            "top_issues": top_issues,
            "summary": summary,
            "metrics": metrics,
            "score": score,
            "chart_columns": chart_columns,
            "score_chart": score_chart,
            "versions": versions,
            "analysis_history": analysis_history,
            "active_nav": "datasets",
        },
    )


@login_required
def column_detail(request, pk: int, profile_id: int):
    dataset = _owned_dataset(request, pk)
    profile = get_object_or_404(
        ColumnProfile.objects.select_related(
            "analysis_run", "analysis_run__dataset_version", "analysis_run__dataset_version__dataset"
        ),
        pk=profile_id,
        analysis_run__dataset_version__dataset=dataset,
    )
    related_issues = profile.analysis_run.quality_issues.filter(
        column_name=profile.column_name
    )
    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    related_issues = sorted(
        related_issues,
        key=lambda issue: (severity_order.get(issue.severity, 5), issue.rule_id),
    )
    numeric_stats = (profile.summary_statistics or {}).get("numeric")
    date_stats = (profile.summary_statistics or {}).get("datetime")
    text_stats = (profile.summary_statistics or {}).get("text", {})
    boolean_stats = (profile.summary_statistics or {}).get("boolean")
    return render(
        request,
        "profiling/column_detail.html",
        {
            "dataset": dataset,
            "version": profile.analysis_run.dataset_version,
            "run": profile.analysis_run,
            "profile": profile,
            "related_issues": related_issues,
            "numeric_stats": numeric_stats,
            "date_stats": date_stats,
            "text_stats": text_stats,
            "boolean_stats": boolean_stats,
            "active_nav": "datasets",
        },
    )
