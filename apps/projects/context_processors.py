from __future__ import annotations

from django.utils import timezone


def navigation(request) -> dict:
    context = {
        "product_name": "DataSieve",
        "current_year": timezone.now().year,
    }
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from .models import Project

        context["nav_projects"] = Project.objects.filter(owner=user).only(
            "id", "name", "updated_at"
        )[:6]
        context["nav_project_count"] = Project.objects.filter(owner=user).count()
    else:
        context["nav_projects"] = []
        context["nav_project_count"] = 0
    return context
