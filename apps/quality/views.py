from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.datasets.models import AnalysisStatus, Dataset
from apps.profiling.models import AnalysisRun

from .models import QualityIssue
from .services.rules import RULES


def _run_context(request, dataset: Dataset, version_id: str | None):
    versions = dataset.versions.all()
    if version_id:
        version = get_object_or_404(versions, pk=version_id)
    else:
        version = versions.order_by("-version_number").first()
        if not version:
            raise Http404("Dataset version not found.")
    run = (
        version.analysis_runs.select_related("dataset_version")
        .order_by("-started_at", "-pk")
        .first()
    )
    return version, run


@login_required
def findings(request, pk: int):
    dataset = get_object_or_404(Dataset, pk=pk, owner=request.user)
    version, run = _run_context(request, dataset, request.GET.get("version"))
    issues = QualityIssue.objects.none()
    if run and run.status == AnalysisStatus.COMPLETED:
        issues = QualityIssue.objects.filter(analysis_run=run)
    severity = request.GET.get("severity") or ""
    rule_id = request.GET.get("rule") or ""
    status = request.GET.get("status") or ""
    query = (request.GET.get("q") or "").strip()
    if severity in dict(QualityIssue.Severity.choices):
        issues = issues.filter(severity=severity)
    if rule_id in {rule.rule_id for rule in RULES}:
        issues = issues.filter(rule_id=rule_id)
    if status in dict(QualityIssue.Status.choices):
        issues = issues.filter(status=status)
    if query:
        issues = issues.filter(
            Q(column_name__icontains=query)
            | Q(explanation__icontains=query)
            | Q(issue_type__icontains=query)
            | Q(recommendation__icontains=query)
        )
    issues = issues.annotate(
        severity_order=Case(
            When(severity="high", then=Value(0)),
            When(severity="medium", then=Value(1)),
            When(severity="low", then=Value(2)),
            When(severity="info", then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
    ).order_by("severity_order", "rule_id", "column_name")
    paginator = Paginator(issues, 20)
    page = paginator.get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    query_string = params.urlencode()
    severity_counts = {}
    total = 0
    if run and run.status == AnalysisStatus.COMPLETED:
        severity_counts = {
            row["severity"]: row["count"]
            for row in QualityIssue.objects.filter(analysis_run=run)
            .values("severity")
            .annotate(count=Count("id"))
        }
        total = sum(severity_counts.values())

    context = {
        "dataset": dataset,
        "version": version,
        "run": run,
        "page": page,
        "severity": severity,
        "rule_id": rule_id,
        "status": status,
        "query": query,
        "query_string": query_string,
        "severity_choices": QualityIssue.Severity.choices,
        "status_choices": QualityIssue.Status.choices,
        "rules": RULES,
        "severity_counts": severity_counts,
        "issue_total": total,
        "active_nav": "datasets",
    }
    if request.headers.get("HX-Request") == "true":
        return render(request, "quality/_findings_results.html", context)
    return render(request, "quality/findings.html", context)


@login_required
def update_issue_status(request, issue_id: int):
    if request.method != "POST":
        return redirect("projects:dashboard")
    issue = get_object_or_404(
        QualityIssue.objects.select_related(
            "analysis_run__dataset_version__dataset"
        ),
        pk=issue_id,
        analysis_run__dataset_version__dataset__owner=request.user,
    )
    status = request.POST.get("status", "")
    if status not in dict(QualityIssue.Status.choices):
        messages.error(request, "Unsupported issue status.")
        return redirect(
            f"{reverse('quality:findings', args=[issue.analysis_run.dataset_version.dataset_id])}?version={issue.analysis_run.dataset_version_id}"
        )
    if issue.status != status:
        issue.status = status
        issue.save(update_fields=["status"])
        AuditEvent.objects.create(
            owner=request.user,
            dataset=issue.analysis_run.dataset_version.dataset,
            action="quality.issue_status_changed",
            safe_metadata={
                "issue_id": issue.pk,
                "rule_id": issue.rule_id,
                "status": status,
            },
        )
        messages.success(request, "Finding status updated.")
    return redirect(
        f"{reverse('quality:findings', args=[issue.analysis_run.dataset_version.dataset_id])}?version={issue.analysis_run.dataset_version_id}"
    )


def rules(request):
    bands = settings.QUALITY_SCORE_BANDS
    context = {
        "rules": RULES,
        "rule_map": {rule.rule_id: rule for rule in RULES},
        "bands": bands,
        "weights": settings.QUALITY_SCORE_WEIGHTS,
        "active_nav": "rules",
    }
    return render(request, "quality/rules.html", context)
