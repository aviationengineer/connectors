"""Pipeline run metadata.

One :class:`PipelineMetadata` record is produced per execution. It
carries the run id, timing, the LinkedIn organizations covered, record
counts, stage timings, data-quality metrics, output status and any
error, so every run can be audited and later loaded into an analytics
warehouse.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from src.utils.date_utils import format_iso_utc, now_utc

PIPELINE_VERSION = "1.0.0"


@dataclass
class PipelineMetadata:
    """A single pipeline execution record."""

    pipeline_run_id: str
    pipeline_name: str = "linkedin_pages_ingestion"
    pipeline_version: str = PIPELINE_VERSION
    execution_mode: str = "full"  # "full" | "incremental"
    start_timestamp: str | None = None
    end_timestamp: str | None = None
    duration_seconds: float | None = None
    status: str = "RUNNING"  # RUNNING | SUCCESS | FAILED
    source_system: str = "LINKEDIN"
    source_type: str = "COMPANY_PAGE"
    organization_urns: list[str] = field(default_factory=list)
    organization_names: list[str] = field(default_factory=list)
    api_version: str | None = None
    records_extracted: int = 0
    records_transformed: int = 0
    records_validated: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    records_duplicates: int = 0
    output_format: str = "CSV"
    output_location: str | None = None
    google_sheets_enabled: bool = False
    google_sheets_status: str = "DISABLED"  # DISABLED | SUCCESS | FAILED | SKIPPED
    last_successful_run: str | None = None
    current_watermark: str | None = None
    warnings_count: int = 0
    errors_count: int = 0
    retry_count: int = 0
    data_quality_status: str = "NOT_RUN"  # NOT_RUN | PASS | WARN | FAIL
    configuration_version: str = "1.0"
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    host: str = field(default_factory=lambda: platform.node())
    environment: str = "DEV"
    error_message: str | None = None
    error: dict[str, Any] | None = None
    status_message: str | None = None
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)
    created_timestamp: str | None = None

    def __post_init__(self) -> None:
        if self.start_timestamp is None:
            self.start_timestamp = format_iso_utc(now_utc())
        if self.created_timestamp is None:
            self.created_timestamp = self.start_timestamp

    # -- lifecycle ----------------------------------------------------------
    def start_stage(self, name: str) -> datetime:
        """Mark a stage as started; returns the start time for timing."""
        self.stages[name] = {"status": "RUNNING", "started_at": format_iso_utc(now_utc())}
        return now_utc()

    def end_stage(self, name: str, started: datetime, **extra: Any) -> None:
        """Close a stage with its duration and any extra metrics."""
        stage = self.stages.setdefault(name, {})
        stage["status"] = "SUCCESS"
        stage["duration_seconds"] = round((now_utc() - started).total_seconds(), 3)
        stage.update(extra)

    def fail_stage(self, name: str, started: datetime, message: str) -> None:
        """Close a stage as failed."""
        stage = self.stages.setdefault(name, {})
        stage["status"] = "FAILED"
        stage["duration_seconds"] = round((now_utc() - started).total_seconds(), 3)
        stage["error"] = message

    def finish(self, status: str, status_message: str | None = None) -> None:
        """Close the run: stamp the end time and compute the duration."""
        end = now_utc()
        self.end_timestamp = format_iso_utc(end)
        start = self._parse(self.start_timestamp)
        self.duration_seconds = (
            round((end - start).total_seconds(), 3) if start else None
        )
        self.status = status
        if status_message:
            self.status_message = status_message

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise the record; nested dicts/lists are JSON-encoded strings."""
        record = asdict(self)
        for key in ("stages", "data_quality", "error"):
            record[key] = json.dumps(record[key], default=str)
        for key in ("organization_urns", "organization_names"):
            record[key] = json.dumps(record[key], default=str)
        return record

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineMetadata":
        """Rebuild a record from :meth:`to_dict` output."""
        data = dict(data)
        for key in ("stages", "data_quality", "error"):
            value = data.get(key)
            if isinstance(value, str):
                try:
                    data[key] = json.loads(value) if value else {}
                except json.JSONDecodeError:
                    data[key] = {}
        for key in ("organization_urns", "organization_names"):
            value = data.get(key)
            if isinstance(value, str):
                try:
                    data[key] = json.loads(value) if value else []
                except json.JSONDecodeError:
                    data[key] = []
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_json(self) -> str:
        """One-line JSON representation (for the JSONL metadata file)."""
        return json.dumps(self.to_dict(), default=str)
