from __future__ import annotations

from django import forms

from apps.datasets.models import Dataset

from .services.operations import OPERATIONS


class CleaningForm(forms.Form):
    source_version = forms.ChoiceField(label="Source version")
    operation = forms.ChoiceField(
        label="Operation",
        choices=[(definition.operation, definition.label) for definition in OPERATIONS.values()],
    )
    strategy = forms.ChoiceField(
        label="Missing-value strategy",
        required=False,
        choices=[
            ("", "Choose a strategy"),
            ("drop_rows", "Drop rows with missing values"),
            ("constant", "Replace with a constant"),
            ("mean", "Fill numeric values with the mean"),
            ("median", "Fill numeric values with the median"),
            ("mode", "Fill with the most frequent value"),
        ],
    )
    constant_value = forms.CharField(
        label="Constant fill value",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Unknown"}),
    )
    target_type = forms.ChoiceField(
        label="Target type",
        required=False,
        choices=[
            ("", "Choose a target type"),
            ("string", "Text"),
            ("integer", "Integer"),
            ("float", "Float"),
            ("date", "Date"),
            ("boolean", "Boolean"),
        ],
    )
    mapping_text = forms.CharField(
        label="Category mapping",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": '{"Jakarta": "JAKARTA", "jakarta": "JAKARTA"}',
                "spellcheck": "false",
            }
        ),
        help_text='Exact-match JSON object: {"old value": "new value"}. Never evaluated as code.',
    )
    identifier_prefix = forms.CharField(
        label="Pseudonym prefix",
        required=False,
        max_length=12,
        initial="ID-",
        help_text="Optional visible prefix for generated pseudonyms.",
    )

    def __init__(self, dataset: Dataset, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dataset = dataset
        versions = dataset.versions.order_by("-version_number")
        self.fields["source_version"].choices = [
            (
                str(version.pk),
                f"v{version.version_number} · {version.get_version_type_display()} · "
                f"{version.row_count:,} rows",
            )
            for version in versions
        ]
        self.fields["operation"].initial = "trim_whitespace"
