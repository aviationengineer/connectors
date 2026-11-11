"""Pipeline orchestration (spec §13, §20, §43, §52, §55).

:class:`JiraPipeline` owns the execution lifecycle::

    CONFIG → AUTHENTICATE → JIRA API → PAGINATION → RAW JSON → TRANSFORM
    → DATA QUALITY → DEDUPLICATION → UPSERT → CSV → OPTIONAL SHEETS
    → METADATA → LOGGING

The incremental watermark is advanced ONLY when a run succeeds.
A failed run never touches ``latest_run.json`` or
``data/output/metadata.json`` (§55).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.clients.jira_client import JiraClient
from src.handlers.jira_handler import JiraHandler
from src.transformers.jira_transformer import JiraTransformer
from src.utils.config_loader import get_nested, load_config, load_fields_config
from src.utils.data_quality import drop_invalid_rows, validate_dataframe
from src.utils.date_utils import (
    apply_lookback,
    format_iso_utc,
    format_jql_datetime,
    generate_run_id,
    now_utc,
    parse_jira_datetime,
)
from src.utils.exceptions import ConfigError, JiraAPIError, JiraFrameworkError
from src.utils.logger import PipelineLogger, get_logger, set_run_id
from src.utils.pipeline_metadata import PipelineMetadata
from src.writers.csv_writer import CSVWriter
from src.writers.google_sheet_writer import GoogleSheetWriter
from src.writers.metadata_writer import MetadataWriter

logger = get_logger("jira_pipeline.pipeline")

ORDER_BY_RE = re.compile(r"\border\s+by\b.*$", re.IGNORECASE | re.DOTALL)


class _CountingHandler(logging.Handler):
    """Counts WARNING and ERROR records for the run metadata."""

    def __init__(self) -> None:
        super().__init__()
        self.warnings = 0
        self.errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno == logging.WARNING:
            self.warnings += 1
        elif record.levelno >= logging.ERROR:
            self.errors += 1


def print_summary(metadata: PipelineMetadata) -> None:
    """Print the §52 end-of-run summary to the console."""
    lines = [
        "=" * 50,
        "JIRA PIPELINE SUMMARY",
        "=" * 50,
        f"Run ID:              {metadata.pipeline_run_id}",
        f"Pipeline:            {metadata.pipeline_name}",
        f"Mode:                {metadata.execution_mode.title()}",
        f"Status:              {metadata.status}",
        f"Start:               {metadata.start_timestamp}",
        f"End:                 {metadata.end_timestamp}",
        f"Duration:            {metadata.duration_seconds} seconds",
        f"Records Extracted:   {metadata.records_extracted}",
        f"Records Inserted:    {metadata.records_inserted}",
        f"Records Updated:     {metadata.records_updated}",
        f"Records Skipped:     {metadata.records_skipped}",
        f"Records Failed:      {metadata.records_failed}",
        f"Duplicates:          {metadata.records_duplicates}",
        f"Data Quality:        {metadata.data_quality_status}",
        f"CSV:                 {metadata.stages.get('csv_upsert', {}).get('status', 'SKIPPED')}",
        f"Excel:               {metadata.stages.get('excel_write', {}).get('status', 'SKIPPED')}",
        f"Google Sheets:       {metadata.google_sheets_status}",
        f"Warnings:            {metadata.warnings_count}",
        f"Errors:              {metadata.errors_count}",
        f"Retries:             {metadata.retry_count}",
        "=" * 50,
    ]
    print("\n".join(lines))


class JiraPipeline:
    """Orchestrate a full or incremental Jira ingestion run.

    All collaborators are injectable, so tests can substitute fakes
    without touching Jira, CSV or Google Sheets.

    Subclasses specialise the source by overriding :attr:`pipeline_name`,
    :attr:`source_endpoint`, :meth:`_jira_project_label`,
    :meth:`_csv_output_path`, :meth:`_sheets_worksheet_name` and
    :meth:`extract` — the run lifecycle itself is shared.
    """

    pipeline_name = "jira_issue_ingestion"
    source_endpoint = "/rest/api/3/search/jql"

    def __init__(
        self,
        config_path: str | Path | None = None,
        config: dict[str, Any] | None = None,
        handler: JiraHandler | None = None,
        transformer: JiraTransformer | None = None,
        csv_writer: CSVWriter | None = None,
        sheets_writer: GoogleSheetWriter | None = None,
        metadata_writer: MetadataWriter | None = None,
        logger: PipelineLogger | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path else None
        self.config = config or (
            load_config(self.config_path) if self.config_path else {}
        )
        # Project root: explicit override, else <root>/config/config.yaml
        # -> <root>, else the current working directory.
        if project_root is not None:
            self.project_root = Path(project_root).resolve()
        elif self.config_path:
            self.project_root = self.config_path.parent.parent.resolve()
        else:
            self.project_root = Path.cwd()
        self._handler = handler
        self._transformer = transformer
        self._csv_writer = csv_writer
        self._sheets_writer = sheets_writer
        self._metadata_writer = metadata_writer
        self._logger = logger
        self._counter = _CountingHandler()
        self._fields_config: dict[str, Any] | None = None

    # -- lazy collaborators -------------------------------------------------
    @property
    def fields_config(self) -> dict[str, Any]:
        if self._fields_config is None:
            fields_path = get_nested(self.config, "jira", "fields_config")
            if fields_path:
                path = Path(str(fields_path))
            elif self.config_path:
                path = self.config_path.parent / "jira_fields.yaml"
            else:
                path = self.project_root / "config" / "jira_fields.yaml"
            self._fields_config = load_fields_config(path)
        return self._fields_config

    @property
    def transformer(self) -> JiraTransformer:
        if self._transformer is None:
            custom_fields = {
                field_id: spec.get("name", field_id)
                for field_id, spec in (
                    self.fields_config.get("custom_fields") or {}
                ).items()
                if isinstance(spec, dict)
            }
            self._transformer = JiraTransformer(
                base_url=str(get_nested(self.config, "jira", "base_url", default="")),
                custom_fields=custom_fields,
                retain_unknown_custom_fields=bool(
                    self.fields_config.get("retain_unknown_custom_fields", False)
                ),
            )
        return self._transformer

    @property
    def handler(self) -> JiraHandler:
        if self._handler is None:
            jira_cfg = self.config.get("jira", {})
            base_url = str(jira_cfg.get("base_url", "")).strip()
            if not base_url:
                raise ConfigError(
                    "Jira base URL is not configured. Set JIRA_BASE_URL "
                    "in your .env file (see .env.example)."
                )
            email = os.environ.get("JIRA_EMAIL", "").strip()
            token = os.environ.get("JIRA_API_TOKEN", "").strip()
            if not email or not token:
                raise ConfigError(
                    "Jira credentials not configured. Set JIRA_EMAIL and "
                    "JIRA_API_TOKEN in your .env file (see .env.example). "
                    "No credentials are stored in this repository."
                )
            client = JiraClient(
                base_url=base_url,
                username=email,
                api_token=token,
                auth_type=str(jira_cfg.get("auth_type", "basic")),
                timeout=int(jira_cfg.get("timeout", 30)),
            )
            self._handler = JiraHandler(
                base_url=base_url,
                username=email,
                api_token=token,
                auth_type=str(jira_cfg.get("auth_type", "basic")),
                timeout=int(jira_cfg.get("timeout", 30)),
                page_size=int(jira_cfg.get("page_size", 100)),
                client=client,
                transformer=self.transformer,
            )
        return self._handler

    @property
    def csv_writer(self) -> CSVWriter:
        if self._csv_writer is None:
            self._csv_writer = CSVWriter()
        return self._csv_writer

    @property
    def metadata_writer(self) -> MetadataWriter:
        if self._metadata_writer is None:
            self._metadata_writer = MetadataWriter(
                metadata_dir=self.project_root / "data" / "metadata",
                output_metadata_path=self.project_root / "data" / "output" / "metadata.json",
            )
        return self._metadata_writer

    def _jira_project_label(self) -> str | None:
        """Project label recorded in the run metadata."""
        projects = get_nested(self.config, "jira", "projects", default=[]) or []
        return ", ".join(str(p) for p in projects) if projects else None

    def _csv_output_path(self) -> Path:
        """CSV destination for this pipeline's upsert."""
        return self.project_root / str(
            get_nested(
                self.config, "output", "csv", "path",
                default="data/output/jira_issues.csv",
            )
        )

    def _sheets_worksheet_name(self) -> str:
        """Google Sheets worksheet for this pipeline's upsert."""
        return str(
            get_nested(self.config, "google_sheets", "worksheet_name", default="jira_issues")
        )

    def _excel_output_path(self) -> Path:
        """Excel workbook destination for this pipeline's output."""
        return self.project_root / str(
            get_nested(
                self.config, "output", "excel", "path",
                default="data/output/jira_issues.xlsx",
            )
        )

    def _excel_sheet_name(self) -> str:
        """Excel sheet name for this pipeline's output."""
        return str(
            get_nested(self.config, "output", "excel", "sheet_name", default="jira_issues")
        )

    def _build_sheets_writer(self) -> GoogleSheetWriter | None:
        if self._sheets_writer is not None:
            return self._sheets_writer
        sheets_cfg = self.config.get("google_sheets", {})
        if not sheets_cfg.get("enabled"):
            return None
        spreadsheet_id = str(sheets_cfg.get("spreadsheet_id", "")).strip()
        if not spreadsheet_id:
            logger.warning(
                "Google Sheets is enabled but no spreadsheet_id is configured; "
                "skipping the Sheets destination."
            )
            return None
        self._sheets_writer = GoogleSheetWriter(
            spreadsheet_id=spreadsheet_id,
            worksheet_name=self._sheets_worksheet_name(),
            credentials_file=str(
                sheets_cfg.get("credentials_file", "credentials/service_account.json")
            ),
        )
        return self._sheets_writer

    # -- JQL ----------------------------------------------------------------
    def _default_jql(self) -> str:
        configured = get_nested(self.config, "jira", "jql", "issues")
        if configured:
            return str(configured)
        projects = get_nested(self.config, "jira", "projects", default=[]) or []
        if projects:
            quoted = ", ".join(str(p) for p in projects)
            return f"project IN ({quoted}) ORDER BY updated DESC"
        return "ORDER BY updated DESC"

    def _incremental_jql(self, base_jql: str, watermark: datetime) -> str:
        """Append an ``updated >=`` clause (with lookback) to a JQL query."""
        lookback = int(get_nested(self.config, "incremental_load", "lookback_minutes", default=10))
        effective = apply_lookback(watermark, lookback)
        condition = f'updated >= "{format_jql_datetime(effective)}"'
        without_order = ORDER_BY_RE.sub("", base_jql).strip()
        if "updated >=" in without_order or "updated>=" in without_order:
            return f"{without_order} ORDER BY updated ASC"
        return f"{without_order} AND {condition} ORDER BY updated ASC"

    def _read_watermark(self) -> datetime | None:
        """Return the last successful watermark, or None if there is none."""
        last = self.metadata_writer.get_last_successful_run()
        if not last:
            return None
        raw = last.get("current_watermark") or last.get("last_successful_run")
        return parse_jira_datetime(raw) if raw else None

    # -- stages -------------------------------------------------------------
    def extract(self, jql: str, metadata: PipelineMetadata) -> list[dict[str, Any]]:
        """Extract raw issues for a JQL query; optionally preserve raw JSON."""
        started = metadata.start_stage("extraction")
        fields = get_nested(self.config, "jira", "fields", default=None)
        try:
            issues = self.handler.get_all_issues(jql, fields=fields)
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
        path = directory / f"jira_issues_{now.strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "pipeline_run_id": run_id,
            "jql": jql,
            "retrieved_at": format_iso_utc(now),
            "record_count": len(issues),
            "issues": issues,
        }
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
        logger.info("Raw Jira responses preserved at %s", path)

    def transform(
        self, issues: list[dict[str, Any]], metadata: PipelineMetadata
    ) -> pd.DataFrame:
        """Normalise raw issues into the standardized DataFrame."""
        started = metadata.start_stage("transformation")
        try:
            df = self.transformer.transform_issues(issues)
            metadata.records_transformed = len(df)
            metadata.end_stage("transformation", started, records=len(df))
        except Exception as exc:
            metadata.fail_stage("transformation", started, str(exc))
            raise
        return df

    def validate(
        self, df: pd.DataFrame, metadata: PipelineMetadata
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Run data-quality checks; drop invalid rows (never fail on warnings)."""
        started = metadata.start_stage("validation")
        dq = validate_dataframe(df)
        metadata.data_quality = dq
        metadata.data_quality_status = dq["status"]
        metadata.records_validated = dq["valid_records"]
        metadata.records_duplicates = dq["duplicate_issue_id"]
        if dq["status"] == "FAIL":
            metadata.fail_stage("validation", started, "all rows failed validation")
            raise JiraFrameworkError(
                "Data quality validation failed: every record is missing "
                "both issue_id and issue_key."
            )
        if dq["invalid_records"]:
            logger.warning(
                "Dropping %d invalid record(s) (missing issue_id and issue_key)",
                dq["invalid_records"],
            )
            df = drop_invalid_rows(df)
        if dq["duplicate_issue_id"]:
            logger.warning(
                "%d duplicate issue_id(s) detected in the extracted data; "
                "keeping the last occurrence",
                dq["duplicate_issue_id"],
            )
            df = df.drop_duplicates(subset=["issue_id"], keep="last").reset_index(drop=True)
        metadata.end_stage("validation", started, records=len(df))
        return df, dq

    def write(
        self,
        df: pd.DataFrame,
        metadata: PipelineMetadata,
        outputs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Upsert the DataFrame to CSV and, when requested, Google Sheets."""
        outputs = outputs or ["csv"]
        results: dict[str, Any] = {}

        if "csv" in outputs:
            started = metadata.start_stage("csv_upsert")
            csv_path = self._csv_output_path()
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                counts = self.csv_writer.upsert(
                    df, str(csv_path), key_column="issue_id"
                )
                metadata.records_inserted = counts.get("inserted", 0)
                metadata.records_updated = counts.get("updated", 0)
                metadata.records_skipped = counts.get("skipped", 0)
                metadata.output_location = str(csv_path)
                metadata.end_stage("csv_upsert", started, **counts)
                logger.info(
                    "CSV upsert completed: %d inserted, %d updated, %d skipped",
                    counts.get("inserted", 0),
                    counts.get("updated", 0),
                    counts.get("skipped", 0),
                )
            except Exception as exc:
                metadata.fail_stage("csv_upsert", started, str(exc))
                raise
            results["csv"] = counts

        if "google_sheets" in outputs:
            started = metadata.start_stage("google_sheets")
            metadata.google_sheets_enabled = True
            try:
                writer = self._build_sheets_writer()
                if writer is None:
                    metadata.google_sheets_status = "SKIPPED"
                    metadata.end_stage("google_sheets", started, status="SKIPPED")
                else:
                    counts = writer.upsert(df, key_column="issue_id")
                    metadata.google_sheets_status = "SUCCESS"
                    metadata.end_stage("google_sheets", started, **counts)
                    logger.info("Google Sheets upsert completed: %s", counts)
                    results["google_sheets"] = counts
            except Exception as exc:
                metadata.google_sheets_status = "FAILED"
                metadata.fail_stage("google_sheets", started, str(exc))
                logger.error("Google Sheets upsert failed: %s", exc)
                raise
        else:
            metadata.google_sheets_enabled = bool(
                get_nested(self.config, "google_sheets", "enabled", default=False)
            )
            metadata.google_sheets_status = "DISABLED"

        if "excel" in outputs:
            started = metadata.start_stage("excel_write")
            try:
                from src.writers.excel_writer import ExcelWriter

                excel_path = self._excel_output_path()
                counts = ExcelWriter().write(
                    df, excel_path, sheet_name=self._excel_sheet_name()
                )
                metadata.output_location = str(excel_path)
                metadata.end_stage("excel_write", started, **counts)
                logger.info(
                    "Excel write completed: %d rows to %s",
                    counts.get("written", 0),
                    excel_path,
                )
                results["excel"] = counts
            except Exception as exc:
                metadata.fail_stage("excel_write", started, str(exc))
                raise

        return results

    # -- run lifecycle ------------------------------------------------------
    def run(
        self,
        mode: str = "full",
        jql: str | None = None,
        outputs: list[str] | None = None,
    ) -> PipelineMetadata:
        """Execute one pipeline run and return its metadata record."""
        mode = mode.lower()
        if mode not in ("full", "incremental"):
            raise ConfigError(f"Unknown pipeline mode: {mode!r} (use full|incremental)")
        run_id = generate_run_id()
        set_run_id(run_id)
        pl_logger = self._logger or PipelineLogger.from_config(
            self.config.get("logging"), run_id=run_id
        )
        logging.getLogger("jira_pipeline").addHandler(self._counter)
        metadata = PipelineMetadata(
            pipeline_run_id=run_id,
            pipeline_name=self.pipeline_name,
            source_endpoint=self.source_endpoint,
            execution_mode=mode,
            environment=str(
                os.environ.get("ENVIRONMENT")
                or get_nested(self.config, "environment", default="DEV")
            ),
        )
        metadata.jira_base_url = str(get_nested(self.config, "jira", "base_url", default="") or "")
        metadata.jira_project = self._jira_project_label()

        previous = self.metadata_writer.get_last_successful_run()
        if previous:
            metadata.last_successful_run = (
                previous.get("current_watermark")
                or previous.get("last_successful_run")
            )

        try:
            pl_logger.info("Pipeline started (mode=%s, run_id=%s)", mode, run_id)
            base_jql = jql or self._default_jql()
            if mode == "incremental":
                watermark = self._read_watermark()
                if watermark is None:
                    pl_logger.warning(
                        "Incremental metadata unavailable; falling back to "
                        "full extraction"
                    )
                    effective_jql = base_jql
                else:
                    effective_jql = self._incremental_jql(base_jql, watermark)
                    pl_logger.info("Incremental watermark: %s", format_iso_utc(watermark))
            else:
                effective_jql = base_jql
            metadata.jql = effective_jql
            pl_logger.info("Executing JQL: %s", effective_jql)

            self.handler.test_connection()
            pl_logger.info("Jira connection successful")

            issues = self.extract(effective_jql, metadata)
            df = self.transform(issues, metadata)
            df, _dq = self.validate(df, metadata)
            self.write(df, metadata, outputs=outputs or ["csv"])

            # Watermark = max(updated) of successfully written records.
            if not df.empty and "updated" in df.columns:
                latest = pd.to_datetime(df["updated"], utc=True, errors="coerce").max()
                if pd.notna(latest):
                    metadata.current_watermark = format_iso_utc(latest.to_pydatetime())

            metadata.retry_count = getattr(
                getattr(self._handler, "client", None), "retry_count", 0
            )
            metadata.warnings_count = self._counter.warnings
            metadata.errors_count = self._counter.errors
            metadata.finish("SUCCESS", "Pipeline completed successfully")
            self.metadata_writer.complete_run(metadata)
            pl_logger.info("Pipeline completed successfully")
        except Exception as exc:  # noqa: BLE001 — lifecycle must record any failure
            metadata.errors_count = self._counter.errors + 1
            metadata.warnings_count = self._counter.warnings
            metadata.error_message = str(exc)
            metadata.error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "module": "jira_pipeline",
                "function": "run",
            }
            metadata.finish("FAILED", f"Pipeline failed: {exc}")
            # Watermark is deliberately NOT advanced on failure (§55).
            self.metadata_writer.fail_run(metadata, exc)
            pl_logger.exception("Pipeline failed: %s", exc)
        finally:
            print_summary(metadata)
        return metadata

    def run_full_load(self) -> PipelineMetadata:
        """Run a full extraction (all issues matching the configured JQL)."""
        return self.run(mode="full")

    def run_incremental_load(self) -> PipelineMetadata:
        """Run an incremental extraction (only issues updated since the watermark)."""
        return self.run(mode="incremental")
