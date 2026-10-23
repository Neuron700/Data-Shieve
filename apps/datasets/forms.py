from __future__ import annotations

from pathlib import Path

from django import forms
from django.conf import settings

from apps.profiling.services.parser import SUPPORTED_FORMATS, sanitize_original_filename


DELIMITER_CHOICES = (
    ("auto", "Auto-detect"),
    (",", "Comma (,)"),
    (";", "Semicolon (;)"),
    ("\t", "Tab"),
    ("|", "Pipe (|)"),
)


class UploadDatasetForm(forms.Form):
    file = forms.FileField(
        label="Dataset file",
        help_text=f"CSV or XLSX up to {settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "class": "file-input",
                "aria-label": "Dataset file",
                "aria-describedby": "upload-file-help",
            }
        ),
    )
    delimiter = forms.ChoiceField(
        label="CSV delimiter",
        choices=DELIMITER_CHOICES,
        required=False,
        initial="auto",
        help_text="Ignored for XLSX files. Choose a delimiter manually if auto-detection is uncertain.",
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        original_name = sanitize_original_filename(upload.name)
        extension = Path(original_name).suffix.lower().lstrip(".")
        if extension not in SUPPORTED_FORMATS:
            raise forms.ValidationError("Only .csv and .xlsx files are supported.")
        if upload.size <= 0:
            raise forms.ValidationError("The uploaded file is empty.")
        if upload.size > settings.MAX_UPLOAD_SIZE:
            raise forms.ValidationError(
                f"File size exceeds the {settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB limit."
            )
        return upload

    def clean_delimiter(self):
        delimiter = self.cleaned_data.get("delimiter") or "auto"
        allowed = {value for value, _label in DELIMITER_CHOICES}
        if delimiter not in allowed:
            raise forms.ValidationError("Unsupported delimiter.")
        return delimiter


class ConfirmUploadForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput())
    delimiter = forms.ChoiceField(
        label="CSV delimiter",
        choices=DELIMITER_CHOICES,
        required=False,
        initial="auto",
    )
    sheet_name = forms.ChoiceField(required=False, choices=())

    def clean_delimiter(self):
        delimiter = self.cleaned_data.get("delimiter") or "auto"
        if delimiter not in {value for value, _label in DELIMITER_CHOICES}:
            raise forms.ValidationError("Unsupported delimiter.")
        return delimiter
