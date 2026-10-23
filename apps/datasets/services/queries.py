from __future__ import annotations

from apps.datasets.models import AnalysisStatus
from apps.profiling.models import AnalysisRun


def latest_completed_analysis_for_dataset(dataset):
    return (
        AnalysisRun.objects.filter(
            dataset_version__dataset=dataset,
            status=AnalysisStatus.COMPLETED,
        )
        .select_related("dataset_version")
        .order_by("-started_at", "-pk")
        .first()
    )


def latest_completed_analysis_map(datasets) -> dict[int, AnalysisRun]:
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
    result: dict[int, AnalysisRun] = {}
    for run in runs:
        dataset_id = run.dataset_version.dataset_id
        if dataset_id not in result:
            result[dataset_id] = run
    return result
