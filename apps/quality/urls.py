from django.urls import path

from . import views

app_name = "quality"

urlpatterns = [
    path("datasets/<int:pk>/findings/", views.findings, name="findings"),
    path("findings/<int:issue_id>/status/", views.update_issue_status, name="issue_status"),
    path("quality-rules/", views.rules, name="rules"),
]
