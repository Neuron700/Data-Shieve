from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.SignUpView.as_view(), name="signup"),
    path("login/", views.DataSieveLoginView.as_view(), name="login"),
    path("logout/", views.DataSieveLogoutView.as_view(), name="logout"),
    path(
        "password/change/",
        views.DataSievePasswordChangeView.as_view(),
        name="password_change",
    ),
    path("settings/", views.settings_view, name="settings"),
]
