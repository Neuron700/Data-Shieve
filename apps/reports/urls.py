from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("datasets/<int:pk>/reports/", views.reports_page, name="index"),
    path(
        "datasets/<int:pk>/reports/export/<str:file_format>/",
        views.export_version,
        name="export_version",
    ),
    path(
        "datasets/<int:pk>/reports/profile.json",
        views.profiling_report_json,
        name="profile_json",
    ),
    path(
        "datasets/<int:pk>/reports/quality.<str:file_format>",
        views.quality_report,
        name="quality",
    ),
    path(
        "datasets/<int:pk>/reports/cleaning.md",
        views.cleaning_summary_markdown,
        name="cleaning_markdown",
    ),
]
