from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.projects.urls")),
    path("datasets/", include("apps.datasets.urls")),
    path("", include("apps.profiling.urls")),
    path("", include("apps.quality.urls")),
    path("", include("apps.cleaning.urls")),
    path("", include("apps.reports.urls")),
]
