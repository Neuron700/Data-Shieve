from django.urls import path

from . import views

app_name = "cleaning"

urlpatterns = [
    path("datasets/<int:pk>/clean/", views.workspace, name="workspace"),
    path("cleaning/jobs/<int:pk>/preview/", views.job_preview, name="preview"),
    path("cleaning/jobs/<int:pk>/apply/", views.apply_job, name="apply"),
    path("cleaning/jobs/<int:pk>/discard/", views.discard_job, name="discard"),
]
