"""Read a stored dataset version using its recorded parse configuration."""

from __future__ import annotations

from apps.datasets.models import DatasetVersion

from .parser import DatasetParseError, ParsedDataset, parse_dataset


def parse_metadata(version: DatasetVersion) -> dict:
    summary = version.processing_summary or {}
    parse_meta = summary.get("parse")
    if isinstance(parse_meta, dict):
        return parse_meta
    return summary if summary.get("file_format") else {}


def load_dataset_version(version: DatasetVersion) -> ParsedDataset:
    metadata = parse_metadata(version)
    path = version.path
    if not path.exists():
        raise DatasetParseError("The dataset file is missing from private storage.")
    return parse_dataset(
        path,
        version.dataset.file_format,
        delimiter=metadata.get("delimiter"),
        encoding=metadata.get("encoding"),
        sheet_name=metadata.get("sheet_name"),
    )
