from django.urls import path

from . import views

app_name = "datasets"

urlpatterns = [
    path("", views.dataset_list, name="list"),
    path("upload/<int:project_id>/", views.upload, name="upload"),
    path("<int:pk>/", views.dataset_detail, name="detail"),
    path("<int:pk>/preview/", views.dataset_preview, name="preview"),
    path("<int:pk>/analyze/", views.analyze, name="analyze"),
    path("<int:pk>/versions/", views.dataset_versions, name="versions"),
    path("<int:pk>/delete/", views.dataset_delete, name="delete"),
]
