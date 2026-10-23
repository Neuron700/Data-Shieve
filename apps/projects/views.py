from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.cleaning.models import CleaningJob
from apps.datasets.models import Dataset, DatasetVersion
from apps.profiling.models import AnalysisRun
from apps.quality.models import QualityIssue

from .forms import ProjectForm
from .models import Project


def home(request):
    if request.user.is_authenticated:
        return redirect("projects:dashboard")
    public_stats = {
        "rules": 10,
        "preview_rows": 100,
        "upload_mb": 20,
    }
    return render(request, "landing.html", {"public_stats": public_stats})


def _latest_analysis_map(datasets):
    from apps.datasets.models import AnalysisStatus

    dataset_ids = [dataset.pk for dataset in datasets]
    if not dataset_ids:
        return {}
    runs = (
        AnalysisRun.objects.filter(
            dataset_version__dataset_id__in=dataset_ids,
            status=AnalysisStatus.COMPLETED,
        )
        .select_related("dataset_version", "dataset_version__dataset")
        .order_by("dataset_version__dataset_id", "-started_at", "-pk")
    )
    result = {}
    for run in runs:
        dataset_id = run.dataset_version.dataset_id
        if dataset_id not in result:
            result[dataset_id] = run
    return result


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@login_required
def dashboard(request):
    project_qs = Project.objects.filter(owner=request.user)
    dataset_qs = Dataset.objects.filter(owner=request.user)
    projects_count = project_qs.count()
    datasets_count = dataset_qs.count()
    analyses_count = AnalysisRun.objects.filter(
        dataset_version__dataset__owner=request.user,
        status="completed",
    ).count()
    cleaned_versions_count = DatasetVersion.objects.filter(
        dataset__owner=request.user,
        version_type=DatasetVersion.Type.CLEANED,
    ).count()

    latest_datasets = list(
        dataset_qs.select_related("project").order_by("-created_at")[:6]
    )
    latest_analyses = list(
        AnalysisRun.objects.filter(
            dataset_version__dataset__owner=request.user,
            status="completed",
        )
        .select_related("dataset_version", "dataset_version__dataset", "dataset_version__dataset__project")
        .order_by("-started_at", "-pk")[:8]
    )

    recent_dataset_qs = list(dataset_qs.order_by("-created_at"))
    analysis_map = _latest_analysis_map(recent_dataset_qs)
    latest_run_ids = [run.pk for run in analysis_map.values()]
    latest_runs = list(
        AnalysisRun.objects.filter(pk__in=latest_run_ids).select_related(
            "dataset_version", "dataset_version__dataset"
        )
    )
    score_counts = Counter()
    missing_cells = 0
    duplicate_rows = 0
    scored_datasets = 0
    for run in latest_runs:
        summary = run.summary or {}
        score = summary.get("score") or {}
        category = score.get("category")
        if category:
            score_counts[category] += 1
            scored_datasets += 1
        metrics = summary.get("dataset_metrics") or {}
        missing_cells += int(metrics.get("missing_cells") or 0)
        duplicate_rows += int(metrics.get("duplicate_rows") or 0)

    top_issue_columns = list(
        QualityIssue.objects.filter(analysis_run_id__in=latest_run_ids)
        .exclude(column_name="")
        .values("column_name")
        .annotate(findings=Count("id"), affected=Sum("issue_count"))
        .order_by("-findings", "-affected", "column_name")[:5]
    )

    recent_jobs = list(
        CleaningJob.objects.filter(dataset__owner=request.user)
        .select_related("dataset", "source_version", "output_version")
        .order_by("-created_at")[:5]
    )
    recent_audit = AuditEvent.objects.filter(owner=request.user).select_related("dataset")[:6]

    context = {
        "projects_count": projects_count,
        "datasets_count": datasets_count,
        "analyses_count": analyses_count,
        "cleaned_versions_count": cleaned_versions_count,
        "latest_datasets": latest_datasets,
        "latest_analyses": latest_analyses,
        "score_counts": {
            "Strong": score_counts.get("Strong", 0),
            "Good": score_counts.get("Good", 0),
            "Needs Review": score_counts.get("Needs Review", 0),
            "Poor": score_counts.get("Poor", 0),
        },
        "scored_datasets": scored_datasets,
        "missing_cells": missing_cells,
        "duplicate_rows": duplicate_rows,
        "top_issue_columns": top_issue_columns,
        "recent_jobs": recent_jobs,
        "recent_audit": recent_audit,
        "active_nav": "dashboard",
    }
    return render(request, "dashboard/index.html", context)


@login_required
def project_list(request):
    query = (request.GET.get("q") or "").strip()
    projects = (
        Project.objects.filter(owner=request.user)
        .annotate(dataset_count=Count("datasets", distinct=True))
        .order_by("-updated_at")
    )
    if query:
        projects = projects.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )
    paginator = Paginator(projects, 12)
    page = paginator.get_page(request.GET.get("page"))
    context = {
        "page": page,
        "query": query,
        "total": paginator.count,
        "active_nav": "projects",
    }
    return render(request, "projects/list.html", context)


@login_required
def project_create(request):
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.owner = request.user
            project.save()
            AuditEvent.objects.create(
                owner=request.user,
                action="project.created",
                safe_metadata={"project_id": project.pk},
            )
            messages.success(request, "Project created.")
            return redirect("projects:detail", pk=project.pk)
    else:
        form = ProjectForm()
    return render(
        request,
        "projects/form.html",
        {"form": form, "page_title": "New project", "active_nav": "projects"},
    )


@login_required
def project_detail(request, pk: int):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    datasets = Dataset.objects.filter(project=project, owner=request.user)
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    date_from = _parse_date(request.GET.get("date_from"))
    date_to = _parse_date(request.GET.get("date_to"))
    if query:
        datasets = datasets.filter(original_filename__icontains=query)
    if status in dict(Dataset.Status.choices):
        datasets = datasets.filter(status=status)
    if date_from:
        datasets = datasets.filter(created_at__date__gte=date_from)
    if date_to:
        datasets = datasets.filter(created_at__date__lte=date_to)
    datasets = datasets.select_related("project").order_by("-created_at")
    paginator = Paginator(datasets, 10)
    page = paginator.get_page(request.GET.get("page"))
    page_datasets = list(page.object_list)
    analysis_map = _latest_analysis_map(page_datasets)

    for dataset in page_datasets:
        dataset.latest_analysis_for_card = analysis_map.get(dataset.pk)

    recent_runs = (
        AnalysisRun.objects.filter(dataset_version__dataset__project=project)
        .select_related("dataset_version", "dataset_version__dataset")
        .order_by("-started_at", "-pk")[:8]
    )
    recent_jobs = (
        CleaningJob.objects.filter(dataset__project=project)
        .select_related("dataset", "source_version", "output_version")
        .order_by("-created_at")[:6]
    )
    score = None
    if page_datasets:
        scores = [
            (analysis_map[ds.pk].summary or {}).get("score", {}).get("score")
            for ds in page_datasets
            if ds.pk in analysis_map
        ]
        scores = [value for value in scores if isinstance(value, (int, float))]
        score = round(sum(scores) / len(scores)) if scores else None

    context = {
        "project": project,
        "page": page,
        "query": query,
        "status": status,
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "status_choices": Dataset.Status.choices,
        "analysis_map": analysis_map,
        "recent_runs": recent_runs,
        "recent_jobs": recent_jobs,
        "project_score": score,
        "active_nav": "projects",
    }
    return render(request, "projects/detail.html", context)


@login_required
def project_edit(request, pk: int):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            AuditEvent.objects.create(
                owner=request.user,
                dataset=None,
                action="project.updated",
                safe_metadata={"project_id": project.pk},
            )
            messages.success(request, "Project updated.")
            return redirect("projects:detail", pk=project.pk)
    else:
        form = ProjectForm(instance=project)
    return render(
        request,
        "projects/form.html",
        {
            "form": form,
            "project": project,
            "page_title": "Edit project",
            "active_nav": "projects",
        },
    )


@login_required
def project_delete(request, pk: int):
    project = get_object_or_404(Project, pk=pk, owner=request.user)
    dataset_count = project.datasets.count()
    if request.method == "POST":
        project_id = project.pk
        project.delete()
        AuditEvent.objects.create(
            owner=request.user,
            dataset=None,
            action="project.deleted",
            safe_metadata={"project_id": project_id},
        )
        messages.success(request, "Project and its datasets were deleted.")
        return redirect("projects:list")
    return render(
        request,
        "projects/delete.html",
        {
            "project": project,
            "dataset_count": dataset_count,
            "active_nav": "projects",
        },
    )
