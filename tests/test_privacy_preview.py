from __future__ import annotations

from apps.datasets.services.privacy import redact_preview


def test_redact_preview_masks_sensitive_headers_and_embedded_patterns() -> None:
    preview = {
        "columns": ["email", "notes", "city"],
        "rows": [
            ["ana@example.com", "Call 0812-3456-7890 tomorrow", "Jakarta"],
        ],
        "total_rows": 1,
        "total_columns": 3,
        "shown_rows": 1,
        "truncated": False,
        "limit": 100,
    }

    redacted = redact_preview(preview)

    assert redacted["rows"][0][0] == "[redacted]"
    assert "ana@example.com" not in redacted["rows"][0][0]
    assert "0812-3456-7890" not in redacted["rows"][0][1]
    assert redacted["rows"][0][2] == "Jakarta"
    assert redacted["masked_columns"] == ["email"]
    assert preview["rows"][0][0] == "ana@example.com"
