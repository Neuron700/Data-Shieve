from django.urls import path

from . import views

app_name = "profiling"

urlpatterns = [
    path("datasets/<int:pk>/profile/", views.overview, name="overview"),
    path(
        "datasets/<int:pk>/columns/<int:profile_id>/",
        views.column_detail,
        name="column_detail",
    ),
]
