from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.db.models import Count, Sum
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView

from apps.audit.models import AuditEvent
from apps.datasets.models import Dataset

from .forms import DataSieveLoginForm, ProfileForm, SignUpForm


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("projects:dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("projects:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        AuditEvent.objects.create(
            owner=self.object,
            action="account.registered",
            safe_metadata={},
        )
        messages.success(self.request, "Your DataSieve account is ready.")
        return response


class DataSieveLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = DataSieveLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        messages.success(self.request, "Welcome back.")
        return super().form_valid(form)


class DataSieveLogoutView(LogoutView):
    next_page = reverse_lazy("projects:home")


class DataSievePasswordChangeView(PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:settings")

    def form_valid(self, form):
        messages.success(self.request, "Your password has been updated.")
        return super().form_valid(form)


@login_required
def settings_view(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            AuditEvent.objects.create(
                owner=request.user,
                action="account.profile_updated",
                safe_metadata={},
            )
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:settings")
    else:
        form = ProfileForm(instance=request.user)

    dataset_summary = Dataset.objects.filter(owner=request.user).aggregate(
        total=Count("id"),
        rows=Sum("row_count"),
    )
    context = {
        "form": form,
        "dataset_summary": dataset_summary,
        "active_nav": "settings",
    }
    return render(request, "accounts/settings.html", context)
