"""Specialised board pipeline (Jira Agile board → standardized DataFrame).

:class:`BoardPipeline` is a :class:`JiraPipeline` that sources its issues
from one Jira Agile board (``GET /rest/agile/1.0/board/{id}/issue``)
instead of a platform JQL search. Everything else — transformation,
data-quality checks, CSV upsert, optional Google Sheets, metadata and
the run lifecycle — is shared with the generic pipeline.

Differences from the generic pipeline:

* Issues come from the board endpoint, which paginates by offset
  (``startAt``) rather than by token.
* The board's own filter applies; an optional ``jql`` string further
  filters the board's issues (used for incremental runs).
* Each board keeps its own metadata directory
  (``data/metadata/boards/<board_key>/``) and its own output metadata
  file, so a board watermark never collides with the generic
  pipeline's watermark.

Configuration lives under ``boards:`` in ``config/config.yaml``::

    boards:
      maidp:
        board_id: 8805
        project_key: MAIDP
        jql: "project = MAIDP ORDER BY updated DESC"
        output:
          csv:
            path: data/output/maidp_board_8805_issues.csv
        google_sheets:
          worksheet_name: "MAIDP_Board_8805"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.pipeline.jira_pipeline import JiraPipeline
from src.utils.config_loader import get_nested
from src.utils.date_utils import format_iso_utc, now_utc
from src.utils.exceptions import ConfigError
from src.utils.logger import get_logger
from src.utils.pipeline_metadata import PipelineMetadata
from src.writers.metadata_writer import MetadataWriter

logger = get_logger("jira_pipeline.board_pipeline")


class BoardPipeline(JiraPipeline):
    """Ingest one Jira Agile board's issues into CSV / Google Sheets."""

    pipeline_name = "jira_board_ingestion"

    def __init__(
        self,
        config_path: str | Path | None = None,
        config: dict[str, Any] | None = None,
        board_key: str = "maidp",
        board_id: int | str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            config_path=config_path, config=config, project_root=kwargs.pop("project_root", None),
            handler=kwargs.pop("handler", None),
            transformer=kwargs.pop("transformer", None),
            csv_writer=kwargs.pop("csv_writer", None),
            sheets_writer=kwargs.pop("sheets_writer", None),
            metadata_writer=kwargs.pop("metadata_writer", None),
            logger=kwargs.pop("logger", None),
        )
        if kwargs:
            raise TypeError(f"Unexpected keyword arguments: {sorted(kwargs)}")
        self.board_key = board_key
        board_cfg = (self.config.get("boards", {}) or {}).get(board_key)
        if board_cfg is None and board_id is None:
            raise ConfigError(
                f"No board {board_key!r} in configuration (boards: section) "
                "and no --board-id given."
            )
        self._board_cfg: dict[str, Any] = dict(board_cfg or {})
        self.board_id = board_id if board_id is not None else self._board_cfg.get("board_id")
        if self.board_id is None:
            raise ConfigError(
                f"Board {board_key!r} has no board_id configured and none was given."
            )
        self.source_endpoint = f"/rest/agile/1.0/board/{self.board_id}/issue"

    # -- board configuration ------------------------------------------------
    @property
    def board_config(self) -> dict[str, Any]:
        """The resolved ``boards.<board_key>`` configuration mapping."""
        return self._board_cfg

    @property
    def project_key(self) -> str | None:
        """The board's project key (``MAIDP``), when configured."""
        value = self._board_cfg.get("project_key")
        return str(value) if value else None

    # -- pipeline specialisation -------------------------------------------
    @property
    def metadata_writer(self) -> MetadataWriter:
        """Board-scoped metadata writer (own ledger + watermark)."""
        if self._metadata_writer is None:
            self._metadata_writer = MetadataWriter(
                metadata_dir=self.project_root / "data" / "metadata" / "boards" / self.board_key,
                output_metadata_path=self.project_root / "data" / "output" / f"{self.board_key}_board_metadata.json",
            )
        return self._metadata_writer

    def _jira_project_label(self) -> str | None:
        return self.project_key

    def _default_jql(self) -> str:
        configured = self._board_cfg.get("jql")
        if configured:
            return str(configured)
        if self.project_key:
            return f"project = {self.project_key} ORDER BY updated DESC"
        return "ORDER BY updated DESC"

    def _csv_output_path(self) -> Path:
        return self.project_root / str(
            get_nested(
                self._board_cfg, "output", "csv", "path",
                default=f"data/output/{self.board_key}_board_issues.csv",
            )
        )

    def _sheets_worksheet_name(self) -> str:
        return str(
            get_nested(
                self._board_cfg, "google_sheets", "worksheet_name",
                default=f"{self.board_key}_board",
            )
        )

    # -- extraction ---------------------------------------------------------
    def extract(self, jql: str, metadata: PipelineMetadata) -> list[dict[str, Any]]:
        """Extract raw issues from the board; optionally preserve raw JSON."""
        started = metadata.start_stage("extraction")
        try:
            issues = self.handler.get_board_issues(self.board_id, jql=jql)
            metadata.records_extracted = len(issues)
            metadata.end_stage("extraction", started, records=len(issues))
        except Exception as exc:
            metadata.fail_stage("extraction", started, str(exc))
            raise
        if get_nested(self.config, "raw_data", "enabled", default=False):
            self._save_raw(issues, jql, metadata.pipeline_run_id)
        return issues

    def _save_raw(
        self, issues: list[dict[str, Any]], jql: str, run_id: str
    ) -> None:
        now = now_utc()
        directory = (
            self.project_root / "data" / "raw" / "jira"
            / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"board_{self.board_id}_issues_{now.strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "pipeline_run_id": run_id,
            "pipeline_name": self.pipeline_name,
            "board_id": self.board_id,
            "board_key": self.board_key,
            "jql": jql,
            "retrieved_at": format_iso_utc(now),
            "record_count": len(issues),
            "issues": issues,
        }
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
        logger.info("Raw board responses preserved at %s", path)

    # -- runs ----------------------------------------------------------------
    def run_board_load(self, mode: str = "full", **kwargs: Any) -> PipelineMetadata:
        """Run a full or incremental load for this board."""
        return self.run(mode=mode, **kwargs)
