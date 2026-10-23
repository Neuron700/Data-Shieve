from django import forms

from .models import Project


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Customer churn research",
                    "maxlength": 160,
                    "autofocus": True,
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "What is this dataset for? This helps keep the workspace organised.",
                }
            ),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Project name cannot be empty.")
        return name
