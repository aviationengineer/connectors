"""Tests for the specialised board pipeline (MAIDP board 8805).

A fake handler stands in for Jira; the real CSVWriter and
MetadataWriter are used, writing into tmp dirs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from src.pipeline.board_pipeline import BoardPipeline
from src.transformers.jira_transformer import JiraTransformer
from src.utils.exceptions import JiraAuthError
from tests.conftest import BASE_URL, CUSTOM_FIELDS

BOARD_ID = 8805


class FakeBoardHandler:
    """Stand-in for JiraHandler: serves canned board issues, records calls."""

    def __init__(self, issues, fail_with=None):
        self.issues = issues
        self.fail_with = fail_with
        self.captured: list[tuple[int | str, str | None]] = []
        self.test_connection_calls = 0

    def test_connection(self) -> bool:
        self.test_connection_calls += 1
        return True

    def get_board_issues(self, board_id, jql=None, page_size=None):
        self.captured.append((board_id, jql))
        if self.fail_with is not None:
            raise self.fail_with
        return list(self.issues)

    def get_board(self, board_id):
        return {"id": board_id, "name": "MAIDP", "type": "scrum"}


def _board_config(tmp_dirs) -> dict:
    return {
        "board_id": BOARD_ID,
        "project_key": "MAIDP",
        "name": "MAIDP — Data Product: Maintenance",
        "jql": "project = MAIDP ORDER BY updated DESC",
        "output": {
            "csv": {"path": str(tmp_dirs["output"] / "maidp_board_8805_issues.csv")}
        },
        "google_sheets": {"worksheet_name": "MAIDP_Board_8805"},
    }


def _make_board_pipeline(tmp_config, handler, tmp_path, board_cfg):
    config = copy.deepcopy(tmp_config)
    config["boards"] = {"maidp": board_cfg}
    transformer = JiraTransformer(base_url=BASE_URL, custom_fields=CUSTOM_FIELDS)
    return BoardPipeline(
        config=config,
        board_key="maidp",
        handler=handler,
        transformer=transformer,
        project_root=tmp_path,
    )


def _board_csv_path(tmp_config, tmp_dirs) -> Path:
    return tmp_dirs["output"] / "maidp_board_8805_issues.csv"


def _seed_board_watermark(
    tmp_path, watermark: str = "2026-11-09T14:32:00Z"
) -> Path:
    """Write a successful board watermark into the board metadata dir."""
    path = tmp_path / "data" / "metadata" / "boards" / "maidp" / "latest_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pipeline_run_id": "20261109_143200_cd34",
                "pipeline_name": "jira_board_ingestion",
                "status": "SUCCESS",
                "execution_mode": "full",
                "current_watermark": watermark,
                "last_successful_run": watermark,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_board_full_load_writes_board_csv(
    tmp_config, tmp_dirs, sample_issues
):
    handler = FakeBoardHandler(sample_issues)
    pipeline = _make_board_pipeline(
        tmp_config, handler, tmp_dirs["root"], _board_config(tmp_dirs)
    )
    metadata = pipeline.run_full_load()

    assert metadata.status == "SUCCESS"
    assert metadata.pipeline_name == "jira_board_ingestion"
    assert "8805" in metadata.source_endpoint
    assert metadata.jira_project == "MAIDP"
    assert metadata.records_extracted == 3
    assert metadata.records_inserted == 3
    assert handler.test_connection_calls == 1
    assert handler.captured == [(BOARD_ID, "project = MAIDP ORDER BY updated DESC")]
    df = pd.read_csv(_board_csv_path(tmp_config, tmp_dirs))
    assert len(df) == 3
    assert list(df["issue_key"]) == ["DATA-101", "DATA-102", "DATA-103"]


def test_board_second_run_upserts_without_duplicates(
    tmp_config, tmp_dirs, sample_issues
):
    handler = FakeBoardHandler(copy.deepcopy(sample_issues))
    pipeline = _make_board_pipeline(
        tmp_config, handler, tmp_dirs["root"], _board_config(tmp_dirs)
    )
    pipeline.run_full_load()

    handler.issues[0]["fields"]["summary"] = "Build the ingestion pipeline (revised)"
    new_issue = copy.deepcopy(sample_issues[1])
    new_issue["id"] = "10004"
    new_issue["key"] = "DATA-104"
    new_issue["fields"]["summary"] = "Fourth board issue"
    handler.issues.append(new_issue)

    metadata = pipeline.run_full_load()
    assert metadata.status == "SUCCESS"
    assert metadata.records_inserted == 1
    assert metadata.records_updated == 1
    df = pd.read_csv(_board_csv_path(tmp_config, tmp_dirs))
    assert len(df) == 4
    assert df["issue_key"].is_unique
    assert (
        df.loc[df["issue_key"] == "DATA-101", "summary"].iloc[0]
        == "Build the ingestion pipeline (revised)"
    )


def test_board_incremental_uses_watermark_jql(
    tmp_config, tmp_dirs, sample_issues
):
    _seed_board_watermark(tmp_dirs["root"])
    handler = FakeBoardHandler(sample_issues)
    pipeline = _make_board_pipeline(
        tmp_config, handler, tmp_dirs["root"], _board_config(tmp_dirs)
    )
    metadata = pipeline.run_incremental_load()

    assert metadata.status == "SUCCESS"
    assert len(handler.captured) == 1
    _board_id, jql = handler.captured[0]
    assert _board_id == BOARD_ID
    assert "project = MAIDP" in jql
    # 10-minute lookback on the 14:32 watermark.
    assert 'updated >= "2026-11-09 14:22"' in jql
    assert "ORDER BY updated ASC" in jql


def test_board_failed_run_preserves_watermark(
    tmp_config, tmp_dirs, sample_issues
):
    seed = _seed_board_watermark(tmp_dirs["root"])
    handler = FakeBoardHandler(sample_issues, fail_with=JiraAuthError("bad credentials"))
    pipeline = _make_board_pipeline(
        tmp_config, handler, tmp_dirs["root"], _board_config(tmp_dirs)
    )
    metadata = pipeline.run_full_load()

    assert metadata.status == "FAILED"
    record = json.loads(seed.read_text(encoding="utf-8"))
    assert record["current_watermark"] == "2026-11-09T14:32:00Z"
    assert record["status"] == "SUCCESS"


def test_board_metadata_is_separate_from_generic(
    tmp_config, tmp_dirs, sample_issues
):
    handler = FakeBoardHandler(sample_issues)
    pipeline = _make_board_pipeline(
        tmp_config, handler, tmp_dirs["root"], _board_config(tmp_dirs)
    )
    pipeline.run_full_load()

    board_latest = (
        tmp_dirs["root"] / "data" / "metadata" / "boards" / "maidp" / "latest_run.json"
    )
    generic_latest = tmp_dirs["root"] / "data" / "metadata" / "latest_run.json"
    assert board_latest.is_file()
    assert json.loads(board_latest.read_text(encoding="utf-8"))["pipeline_name"] == (
        "jira_board_ingestion"
    )
    assert not generic_latest.exists()
