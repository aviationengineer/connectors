"""Pipeline run metadata persistence (spec §39–§45, §53, §55).

Three artefacts live under ``metadata_dir``:

* ``pipeline_runs.csv`` — append-only audit history, one row per run.
  The header is the dataclass field order of
  :meth:`PipelineMetadata.to_dict`.
* ``pipeline_runs.json`` — append-only JSONL, one object per line.
* ``latest_run.json`` — watermark file, overwritten ONLY on success
  (``complete_run``); a failed run never touches it (§55).

``output_metadata_path`` (default ``data/output/metadata.json``) is a
small summary for downstream consumers, also written only on success.
All write methods are best-effort: they log the error and return False
instead of raising, so a metadata failure never fails the pipeline run
it is recording.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger
from src.utils.pipeline_metadata import PipelineMetadata

logger = get_logger("jira_pipeline.metadata_writer")


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO timestamp (naive values are treated as UTC)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class MetadataWriter:
    """Persist :class:`PipelineMetadata` records to CSV / JSONL / JSON.

    Args:
        metadata_dir: Directory holding ``pipeline_runs.csv``,
            ``pipeline_runs.json`` and ``latest_run.json``. Created on
            init (best-effort: a failure is logged, not raised, so a
            bad path surfaces as False returns from the write methods).
        output_metadata_path: Summary JSON written on successful runs.
    """

    def __init__(
        self,
        metadata_dir: str | Path = "data/metadata",
        output_metadata_path: str | Path = "data/output/metadata.json",
    ) -> None:
        self.metadata_dir = Path(metadata_dir)
        self.output_metadata_path = Path(output_metadata_path)
        self.runs_csv_path = self.metadata_dir / "pipeline_runs.csv"
        self.runs_json_path = self.metadata_dir / "pipeline_runs.json"
        self.latest_run_path = self.metadata_dir / "latest_run.json"
        try:
            self.metadata_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Could not create metadata directory %s: %s",
                self.metadata_dir,
                exc,
            )

    # -- lifecycle hooks ----------------------------------------------------
    def create_run(self, metadata: PipelineMetadata) -> PipelineMetadata:
        """Lifecycle hook: creation happens in the pipeline; return as-is."""
        return metadata

    def update_run(self, metadata: PipelineMetadata, **fields: Any) -> PipelineMetadata:
        """Set the given fields on the record and return it."""
        for name, value in fields.items():
            setattr(metadata, name, value)
        return metadata

    def complete_run(self, metadata: PipelineMetadata) -> bool:
        """Persist a finished run: append the record, update the watermark.

        Finishing the record (``metadata.finish("SUCCESS")``) is the
        pipeline's job; this method appends the record and — only when
        its status is SUCCESS — writes ``latest_run.json`` and the output
        metadata summary. Returns True only when every write succeeded.
        """
        appended = self.append_run(metadata)
        if metadata.status != "SUCCESS":
            logger.warning(
                "complete_run called for run %s with status %s; "
                "watermark not updated",
                metadata.pipeline_run_id,
                metadata.status,
            )
            return appended
        latest_ok = self._write_latest_run(metadata)
        output_ok = self.write_output_metadata(metadata)
        return bool(appended and latest_ok and output_ok)

    def fail_run(
        self, metadata: PipelineMetadata, error: Exception | None = None
    ) -> bool:
        """Append the FAILED record; never touch the watermark (§55).

        ``latest_run.json`` and the output metadata summary keep the last
        successful run, so a failure cannot move the watermark backwards.
        Returns True when the record was appended.
        """
        if error is not None:
            metadata.error_message = str(error)
            metadata.error = {
                "type": type(error).__name__,
                "message": str(error),
            }
        if metadata.status == "RUNNING":
            metadata.finish("FAILED", str(error) if error else None)
        return self.append_run(metadata)

    # -- append-only history --------------------------------------------------
    def append_run(self, metadata: PipelineMetadata) -> bool:
        """Append one record to the CSV history and the JSONL history.

        The CSV header is written first when the file is new or empty.
        Never raises: returns False on any failure.
        """
        try:
            record = metadata.to_dict()
            csv_is_new = (
                not self.runs_csv_path.is_file()
                or self.runs_csv_path.stat().st_size == 0
            )
            with self.runs_csv_path.open(
                "a", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=list(record.keys()))
                if csv_is_new:
                    writer.writeheader()
                writer.writerow(record)
            with self.runs_json_path.open("a", encoding="utf-8") as handle:
                handle.write(metadata.to_json() + "\n")
            return True
        except Exception as exc:  # noqa: BLE001 — never raise from metadata
            logger.error(
                "Failed to append pipeline run %s to metadata: %s",
                metadata.pipeline_run_id,
                exc,
            )
            return False

    # -- watermark / summary --------------------------------------------------
    def get_last_successful_run(self) -> dict | None:
        """Return the last SUCCESS record, or None when there is none.

        Prefers ``latest_run.json`` when it holds a SUCCESS record, then
        scans ``pipeline_runs.json`` from the end for the last SUCCESS.
        """
        try:
            if self.latest_run_path.is_file():
                record = json.loads(
                    self.latest_run_path.read_text(encoding="utf-8")
                )
                if isinstance(record, dict) and record.get("status") == "SUCCESS":
                    return record
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read latest run metadata: %s", exc)
        try:
            if self.runs_json_path.is_file():
                lines = self.runs_json_path.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict) and record.get("status") == "SUCCESS":
                        return record
        except OSError as exc:
            logger.warning("Could not read pipeline run history: %s", exc)
        return None

    def write_output_metadata(self, metadata: PipelineMetadata) -> bool:
        """Write the output summary JSON (success runs only).

        The summary carries the run id as ``last_successful_run``, the
        transformed record count as ``records_processed`` and the status
        in lowercase (``"success"``). Never raises: False on failure.
        """
        payload = {
            "last_successful_run": metadata.pipeline_run_id,
            "records_processed": metadata.records_transformed,
            "status": str(metadata.status).lower(),
        }
        try:
            self.output_metadata_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_metadata_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            return True
        except Exception as exc:  # noqa: BLE001 — never raise from metadata
            logger.error(
                "Failed to write output metadata %s: %s",
                self.output_metadata_path,
                exc,
            )
            return False

    def _write_latest_run(self, metadata: PipelineMetadata) -> bool:
        """Overwrite ``latest_run.json`` with this (successful) record."""
        try:
            self.latest_run_path.write_text(
                json.dumps(metadata.to_dict(), indent=2, default=str),
                encoding="utf-8",
            )
            return True
        except Exception as exc:  # noqa: BLE001 — never raise from metadata
            logger.error(
                "Failed to write latest run metadata %s: %s",
                self.latest_run_path,
                exc,
            )
            return False

    # -- retention --------------------------------------------------------------
    def prune_old_metadata(self, retention_days: int = 365) -> int:
        """Drop ``pipeline_runs.json`` lines older than ``retention_days``.

        Best-effort helper (never called automatically): unparseable
        lines and lines without a timestamp are kept. Returns the number
        of lines removed. The CSV history is left untouched.
        """
        try:
            if not self.runs_json_path.is_file():
                return 0
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            lines = self.runs_json_path.read_text(encoding="utf-8").splitlines()
            kept: list[str] = []
            removed = 0
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue
                stamp = _parse_timestamp(
                    record.get("created_timestamp")
                    or record.get("start_timestamp")
                )
                if stamp is not None and stamp < cutoff:
                    removed += 1
                else:
                    kept.append(line)
            if removed:
                self.runs_json_path.write_text(
                    "".join(
                        line if line.endswith("\n") else line + "\n"
                        for line in kept
                    ),
                    encoding="utf-8",
                )
            return removed
        except Exception as exc:  # noqa: BLE001 — best-effort helper
            logger.error("Failed to prune old metadata: %s", exc)
            return 0
