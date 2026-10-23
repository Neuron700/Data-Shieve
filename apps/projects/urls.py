from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("projects/", views.project_list, name="list"),
    path("projects/new/", views.project_create, name="create"),
    path("projects/<int:pk>/", views.project_detail, name="detail"),
    path("projects/<int:pk>/edit/", views.project_edit, name="edit"),
    path("projects/<int:pk>/delete/", views.project_delete, name="delete"),
]
