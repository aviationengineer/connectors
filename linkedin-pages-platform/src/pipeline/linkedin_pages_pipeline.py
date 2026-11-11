"""LinkedIn Pages pipeline: official API -> canonical DataFrame -> CSV.

Workflow
--------
1. Validate configuration and resolve the organisations to cover
   (configured URNs, or every Page the member administers).
2. Test the LinkedIn connection (token + Community Management product).
3. List each Page's posts (newest first), optionally filtered by the
   incremental watermark from the last successful run.
4. Preserve the raw payloads under ``data/raw/linkedin/`` (partitioned
   by organisation and date) so a run can be reprocessed without
   calling LinkedIn again.
5. Transform to the canonical post schema.
6. Validate (data quality gate — a fully invalid frame fails the run).
7. Deduplicate and upsert into CSV on ``post_urn`` (default destination).
8. Optionally upsert into Google Sheets (disabled by default).
9. Write the pipeline metadata record (SUCCESS or FAILED — never a
   false SUCCESS).

Only data the authenticated member is authorised to read is collected:
posts of Company Pages they administer, through LinkedIn's official
API. No scraping, no automation of linkedin.com, no bypass of any
access control.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from src.handlers.linkedin_handler import LinkedInPagesHandler
from src.utils import data_quality
from src.utils.config_loader import get_nested
from src.utils.date_utils import (
    apply_lookback,
    format_iso_utc,
    generate_run_id,
    now_utc,
    parse_linkedin_datetime,
)
from src.utils.exceptions import ConfigError, DataQualityError, LinkedInAPIError
from src.utils.logger import PipelineLogger
from src.utils.pipeline_metadata import PipelineMetadata
from src.writers.csv_writer import CSVWriter
from src.writers.google_sheet_writer import GoogleSheetWriter
from src.writers.metadata_writer import MetadataWriter

DEFAULT_OUTPUT = "data/output/linkedin/linkedin_posts.csv"


class LinkedInPagesPipeline:
    """Run the LinkedIn Company Pages ingestion for configured Pages."""

    def __init__(
        self,
        config: dict[str, Any],
        handler: LinkedInPagesHandler | None = None,
        logger: PipelineLogger | None = None,
    ) -> None:
        self.config = config
        self.linkedin_config = config.get("linkedin", {}) or {}
        self.handler = handler
        self._external_logger = logger
        self.run_id = generate_run_id()
        self.metadata_writer = MetadataWriter(
            metadata_dir=get_nested(config, "paths", "metadata_dir")
            or "data/metadata",
            output_metadata_path=get_nested(config, "paths", "output_metadata")
            or "data/output/metadata.json",
        )
        self.csv_writer = CSVWriter()

    # -- configuration ------------------------------------------------------
    def _resolve_organizations(self) -> list[str]:
        """Organisation URNs to cover, from config (never hard-coded)."""
        configured = self.linkedin_config.get("organizations") or []
        urns: list[str] = []
        for entry in configured:
            if isinstance(entry, str):
                urns.append(LinkedInPagesHandler.organization_urn(entry))
            elif isinstance(entry, dict):
                value = entry.get("organization_urn") or entry.get(
                    "organization_id"
                )
                if value:
                    urns.append(LinkedInPagesHandler.organization_urn(value))
        return urns

    def _output_path(self) -> Path:
        return Path(
            self.linkedin_config.get("output_csv") or DEFAULT_OUTPUT
        )

    def _raw_dir(self) -> Path:
        return Path(
            get_nested(self.config, "paths", "raw_dir") or "data/raw/linkedin"
        )

    def _watermarks(self) -> dict[str, str]:
        """Per-organisation ``created_at`` watermarks from the last success."""
        last = self.metadata_writer.get_last_successful_run()
        if not last:
            return {}
        raw = last.get("current_watermark")
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return raw if isinstance(raw, dict) else {}

    # -- run ------------------------------------------------------------------
    def run(
        self,
        mode: str = "full",
        organizations: list[str] | None = None,
        max_posts: int | None = None,
    ) -> dict[str, Any]:
        """Execute one ingestion run; returns the metadata record as a dict."""
        if mode not in ("full", "incremental"):
            raise ConfigError(f"Unknown execution mode: {mode!r}")
        logger = self._external_logger or PipelineLogger.from_config(
            self.config.get("logging"), run_id=self.run_id
        )
        metadata = PipelineMetadata(
            pipeline_run_id=self.run_id,
            pipeline_name="linkedin_pages_ingestion",
            execution_mode=mode,
            environment=str(self.config.get("environment", "DEV")),
            api_version=self.handler.api_version if self.handler else None,
        )
        try:
            self._run(logger, metadata, mode, organizations, max_posts)
        except Exception as exc:  # noqa: BLE001 — metadata must record it
            metadata.errors_count += 1
            metadata.error_message = str(exc)
            metadata.error = {"type": type(exc).__name__, "message": str(exc)}
            metadata.finish("FAILED", str(exc))
            self.metadata_writer.fail_run(metadata, error=exc)
            logger.exception("LinkedIn Pages pipeline failed: %s", exc)
            raise
        return metadata.to_dict()

    def _run(
        self,
        logger: PipelineLogger,
        metadata: PipelineMetadata,
        mode: str,
        organizations: list[str] | None,
        max_posts: int | None,
    ) -> None:
        if self.handler is None:
            token = self._access_token()
            self.handler = LinkedInPagesHandler(
                access_token=token,
                api_version=str(
                    get_nested(self.config, "linkedin", "api_version")
                    or "202610"
                ),
                timeout=int(get_nested(self.config, "linkedin", "timeout") or 30),
            )
        metadata.api_version = self.handler.api_version
        max_posts = max_posts if max_posts is not None else (
            self.linkedin_config.get("max_posts")
        )
        page_size = int(self.linkedin_config.get("page_size", 50))

        # 1. Connection ------------------------------------------------------
        stage = metadata.start_stage("test_connection")
        try:
            connection = self.handler.test_connection()
            metadata.end_stage(
                "test_connection",
                stage,
                administered_pages=connection["administered_pages"],
            )
        except Exception as exc:
            metadata.fail_stage("test_connection", stage, str(exc))
            raise
        logger.info(
            "LinkedIn connection successful: %d administered page(s)",
            connection["administered_pages"],
        )

        # 2. Resolve organisations -------------------------------------------
        org_urns = organizations or self._resolve_organizations()
        if not org_urns:
            org_urns = [
                page["organization_urn"]
                for page in connection["pages"]
            ]
        if not org_urns:
            raise ConfigError(
                "No organisations to ingest: configure "
                "linkedin.organizations in config/linkedin_pages.yaml, or "
                "administer at least one LinkedIn Company Page."
            )
        metadata.organization_urns = org_urns
        logger.info("Ingesting %d LinkedIn page(s): %s", len(org_urns), org_urns)

        watermarks = self._watermarks() if mode == "incremental" else {}
        lookback = int(
            get_nested(self.config, "linkedin", "incremental_lookback_minutes")
            or 10
        )

        # 3-4. Extract + preserve raw ----------------------------------------
        stage = metadata.start_stage("extract")
        frames: list[pd.DataFrame] = []
        new_watermarks: dict[str, str] = dict(watermarks)
        try:
            for org_urn in org_urns:
                org_name = ""
                organization: dict[str, Any] | None = None
                try:
                    organization = self.handler.get_organization(org_urn)
                    org_name = str(organization.get("localizedName", "") or "")
                except LinkedInAPIError as exc:
                    logger.warning(
                        "Could not load details for %s (%s)",
                        org_urn,
                        exc,
                    )
                posts = self.handler.list_all_posts(
                    org_urn, max_posts=max_posts, page_size=page_size
                )
                self._preserve_raw(org_urn, posts)
                if mode == "incremental" and org_urn in watermarks:
                    watermark = parse_linkedin_datetime(watermarks[org_urn])
                    if watermark is not None:
                        cutoff = apply_lookback(watermark, lookback)
                        posts = [
                            post
                            for post in posts
                            if (
                                parse_linkedin_datetime(
                                    post.get(
                                        "publishedAt",
                                        post.get("createdAt", post.get("created")),
                                    )
                                )
                                or now_utc()
                            )
                            > cutoff
                        ]
                if organization is not None:
                    metadata.organization_names.append(org_name)
                if posts:
                    dates = [
                        parse_linkedin_datetime(
                            post.get(
                                "publishedAt",
                                post.get("createdAt", post.get("created")),
                            )
                        )
                        for post in posts
                    ]
                    latest = max(
                        (date for date in dates if date is not None),
                        default=None,
                    )
                    if latest is not None:
                        new_watermarks[org_urn] = format_iso_utc(latest) or ""
                    frames.append(
                        self.handler.posts_to_dataframe(posts, organization)
                    )
                metadata.records_extracted += len(posts)
            metadata.end_stage(
                "extract", stage, records_extracted=metadata.records_extracted
            )
        except Exception as exc:
            metadata.fail_stage("extract", stage, str(exc))
            raise
        logger.info(
            "Extracted %d post(s) across %d page(s)",
            metadata.records_extracted,
            len(org_urns),
        )

        # 5. Transform ---------------------------------------------------------
        stage = metadata.start_stage("transform")
        try:
            if frames:
                frame = pd.concat(frames, ignore_index=True)
            else:
                from src.transformers.linkedin_transformer import SCHEMA_COLUMNS

                frame = pd.DataFrame(columns=SCHEMA_COLUMNS)
            metadata.records_transformed = len(frame)
            metadata.end_stage(
                "transform", stage, records_transformed=len(frame)
            )
        except Exception as exc:
            metadata.fail_stage("transform", stage, str(exc))
            raise

        # 6. Validate -----------------------------------------------------------
        stage = metadata.start_stage("validate")
        try:
            quality = data_quality.validate_dataframe(frame)
            metadata.data_quality = quality
            metadata.data_quality_status = quality["status"]
            metadata.records_validated = quality["valid_records"]
            metadata.records_failed = quality["invalid_records"]
            metadata.records_duplicates = quality["duplicate_business_key"]
            if quality["status"] == "FAIL":
                raise DataQualityError(
                    "Data quality FAILED: "
                    f"{quality['invalid_records']}/{quality['total_records']} "
                    "records invalid (missing post_urn)."
                )
            metadata.end_stage("validate", stage, **quality)
        except Exception as exc:
            metadata.fail_stage("validate", stage, str(exc))
            raise
        logger.info(
            "Data quality %s: %d valid / %d total",
            quality["status"],
            quality["valid_records"],
            quality["total_records"],
        )

        # 7. Deduplicate + upsert CSV -------------------------------------------
        stage = metadata.start_stage("write_csv")
        try:
            frame = data_quality.drop_invalid_rows(frame)
            frame = frame.drop_duplicates(subset=["post_urn"], keep="last")
            output_path = self._output_path()
            counts = self.csv_writer.upsert(
                frame, output_path, key_column="post_urn"
            )
            metadata.records_inserted = counts["inserted"]
            metadata.records_updated = counts["updated"]
            metadata.records_skipped = counts["skipped"]
            metadata.output_location = str(output_path)
            metadata.current_watermark = json.dumps(new_watermarks)
            metadata.end_stage("write_csv", stage, **counts)
        except Exception as exc:
            metadata.fail_stage("write_csv", stage, str(exc))
            raise
        logger.info(
            "CSV upsert completed: inserted=%d updated=%d skipped=%d -> %s",
            counts["inserted"],
            counts["updated"],
            counts["skipped"],
            output_path,
        )

        # 8. Optional Google Sheets ----------------------------------------------
        sheets_cfg = self.config.get("google_sheets") or {}
        if sheets_cfg.get("enabled"):
            stage = metadata.start_stage("write_google_sheets")
            try:
                writer = GoogleSheetWriter(
                    spreadsheet_id=str(sheets_cfg.get("spreadsheet_id", "")),
                    worksheet_name=str(
                        sheets_cfg.get("worksheet_name", "linkedin_posts")
                    ),
                    credentials_file=sheets_cfg.get("credentials_file"),
                )
                counts = writer.upsert(frame, key_column="post_urn")
                metadata.google_sheets_enabled = True
                metadata.google_sheets_status = "SUCCESS"
                metadata.end_stage("write_google_sheets", stage, **counts)
            except Exception as exc:
                metadata.google_sheets_enabled = True
                metadata.google_sheets_status = "FAILED"
                metadata.fail_stage("write_google_sheets", stage, str(exc))
                metadata.errors_count += 1
                logger.error("Google Sheets upsert failed: %s", exc)
        else:
            metadata.google_sheets_status = "DISABLED"

        # 9. Metadata --------------------------------------------------------------
        metadata.finish("SUCCESS", "LinkedIn Pages pipeline completed")
        self.metadata_writer.complete_run(metadata)
        logger.info("LinkedIn Pages pipeline completed successfully")

    # -- helpers ------------------------------------------------------------
    def _access_token(self) -> str:
        """Access token from config env expansion or the environment."""
        from src.utils.config_loader import require_env

        return require_env("LINKEDIN_ACCESS_TOKEN")

    def _preserve_raw(self, org_urn: str, posts: list[dict[str, Any]]) -> Path:
        """Write the raw post payloads for one org, partitioned by date."""
        org_id = org_urn.split(":")[-1] or "unknown"
        now = now_utc()
        directory = (
            self._raw_dir()
            / org_id
            / f"{now.year:04d}"
            / f"{now.month:02d}"
            / f"{now.day:02d}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"run_{self.run_id}.json"
        payload = {
            "pipeline_run_id": self.run_id,
            "organization_urn": org_urn,
            "execution_timestamp": format_iso_utc(now),
            "record_count": len(posts),
            "posts": posts,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path


def run_summary(metadata: dict[str, Any]) -> str:
    """Render the CLI summary block for a finished run."""
    names = metadata.get("organization_names") or []
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except json.JSONDecodeError:
            names = [names]
    lines = [
        "=" * 50,
        "LINKEDIN PAGES PIPELINE",
        "=" * 50,
        f"Pipeline Run ID: {metadata.get('pipeline_run_id')}",
        f"Organisations: {', '.join(names) or '-'}",
        f"Status: {metadata.get('status')}",
        f"Records: {metadata.get('records_extracted')}",
        f"Inserted: {metadata.get('records_inserted')}",
        f"Updated: {metadata.get('records_updated')}",
        f"Skipped: {metadata.get('records_skipped')}",
        f"Failed: {metadata.get('records_failed')}",
        f"Data Quality: {metadata.get('data_quality_status')}",
        f"CSV: {metadata.get('output_location')}",
        f"Pipeline Status: {metadata.get('status')}",
        "=" * 50,
    ]
    return "\n".join(lines)
