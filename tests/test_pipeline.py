"""Tests for the pipeline orchestration (upsert, watermark, failures).

The pipeline and writer modules are imported lazily inside each test:
they are being written in parallel, so collection must not fail when
they are absent — the tests skip cleanly instead. A fake handler
stands in for Jira; the real CSVWriter and MetadataWriter are used.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from src.transformers.jira_transformer import JiraTransformer
from src.utils.exceptions import JiraAuthError
from tests.conftest import BASE_URL, CUSTOM_FIELDS


def _load_pipeline_components():
    """Import the pipeline + writers, or skip if not written yet."""
    try:
        from src.pipeline.jira_pipeline import JiraPipeline
        from src.writers.csv_writer import CSVWriter  # noqa: F401
        from src.writers.metadata_writer import MetadataWriter  # noqa: F401
    except ImportError as exc:
        pytest.skip(f"pipeline/writer modules not available yet: {exc}")
    return JiraPipeline


class FakeHandler:
    """Stand-in for JiraHandler: serves canned issues, records JQL."""

    def __init__(self, issues, fail_with=None):
        self.issues = issues
        self.fail_with = fail_with
        self.captured_jql: list[str] = []
        self.test_connection_calls = 0

    def test_connection(self) -> bool:
        self.test_connection_calls += 1
        return True

    def get_all_issues(self, jql, fields=None, page_size=None):
        self.captured_jql.append(jql)
        if self.fail_with is not None:
            raise self.fail_with
        return list(self.issues)


def _make_pipeline(tmp_config, handler, tmp_path):
    JiraPipeline = _load_pipeline_components()
    transformer = JiraTransformer(base_url=BASE_URL, custom_fields=CUSTOM_FIELDS)
    return JiraPipeline(
        config=tmp_config,
        handler=handler,
        transformer=transformer,
        project_root=tmp_path,
    )


def _csv_path(tmp_config) -> Path:
    return Path(tmp_config["output"]["csv"]["path"])


def _seed_latest_run(tmp_path, watermark: str = "2026-11-09T14:32:00Z") -> Path:
    """Write a plausible latest_run.json with a successful watermark."""
    path = tmp_path / "data" / "metadata" / "latest_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pipeline_run_id": "20261109_143200_ab12",
                "pipeline_name": "jira_issue_ingestion",
                "status": "SUCCESS",
                "execution_mode": "full",
                "current_watermark": watermark,
                "last_successful_run": watermark,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_run_full_load_writes_csv_with_three_rows(
    tmp_config, tmp_dirs, sample_issues
):
    handler = FakeHandler(sample_issues)
    pipeline = _make_pipeline(tmp_config, handler, tmp_dirs["root"])
    metadata = pipeline.run_full_load()

    assert metadata.status == "SUCCESS"
    assert metadata.records_extracted == 3
    assert metadata.records_inserted == 3
    assert handler.test_connection_calls == 1
    df = pd.read_csv(_csv_path(tmp_config))
    assert len(df) == 3
    assert list(df["issue_key"]) == ["DATA-101", "DATA-102", "DATA-103"]


def test_second_run_upserts_without_duplicates(tmp_config, tmp_dirs, sample_issues):
    handler = FakeHandler(copy.deepcopy(sample_issues))
    pipeline = _make_pipeline(tmp_config, handler, tmp_dirs["root"])
    pipeline.run_full_load()

    # One changed issue (summary revised) + one brand-new issue.
    handler.issues[0]["fields"]["summary"] = "Build the ingestion pipeline (revised)"
    new_issue = copy.deepcopy(sample_issues[1])
    new_issue["id"] = "10004"
    new_issue["key"] = "DATA-104"
    new_issue["fields"]["summary"] = "Fourth issue"
    handler.issues.append(new_issue)

    metadata = pipeline.run_full_load()
    assert metadata.status == "SUCCESS"
    df = pd.read_csv(_csv_path(tmp_config))
    assert len(df) == 4
    assert df["issue_id"].is_unique
    revised = df.loc[df["issue_key"] == "DATA-101", "summary"].iloc[0]
    assert revised == "Build the ingestion pipeline (revised)"
    assert "Fourth issue" in list(df["summary"])
    assert metadata.records_updated >= 1


def test_incremental_run_uses_watermark_in_jql(
    tmp_config, tmp_dirs, sample_issues
):
    _seed_latest_run(tmp_dirs["root"])
    handler = FakeHandler(sample_issues)
    pipeline = _make_pipeline(tmp_config, handler, tmp_dirs["root"])
    metadata = pipeline.run_incremental_load()

    assert metadata.status == "SUCCESS"
    assert handler.captured_jql, "handler should have received a JQL query"
    assert "updated >=" in handler.captured_jql[-1]


def test_failed_run_produces_failed_metadata_and_keeps_watermark(
    tmp_config, tmp_dirs, sample_issues
):
    latest = _seed_latest_run(tmp_dirs["root"])
    before = latest.read_bytes()
    handler = FakeHandler(
        sample_issues, fail_with=JiraAuthError("Jira authentication failed (HTTP 401).")
    )
    pipeline = _make_pipeline(tmp_config, handler, tmp_dirs["root"])
    metadata = pipeline.run_full_load()

    assert metadata.status == "FAILED"
    assert metadata.error_message
    assert latest.read_bytes() == before, "watermark must not advance on failure"


def test_empty_extraction_succeeds_with_zero_records(tmp_config, tmp_dirs):
    handler = FakeHandler([])
    pipeline = _make_pipeline(tmp_config, handler, tmp_dirs["root"])
    metadata = pipeline.run_full_load()

    assert metadata.status == "SUCCESS"
    assert metadata.records_extracted == 0
    assert metadata.records_inserted == 0
