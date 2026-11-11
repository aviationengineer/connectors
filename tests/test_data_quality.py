"""Tests for data-quality checks on the standardised DataFrame."""

from __future__ import annotations

import pandas as pd

from src.utils.data_quality import (
    check_required_columns,
    drop_invalid_rows,
    validate_dataframe,
)
from src.transformers.jira_transformer import SCHEMA_COLUMNS


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=SCHEMA_COLUMNS)


def _clean_row(**overrides) -> dict:
    row = {column: None for column in SCHEMA_COLUMNS}
    row.update(
        {
            "issue_id": "10001",
            "issue_key": "DATA-101",
            "summary": "Build the ingestion pipeline",
            "created": "2026-11-01T09:00:00Z",
            "updated": "2026-11-09T14:32:00Z",
            "assignee": "Alex Assignee",
            "reporter": "Priya Reporter",
            "status": "In Progress",
        }
    )
    row.update(overrides)
    return row


def test_clean_frame_passes_with_zero_invalid(sample_dataframe):
    result = validate_dataframe(sample_dataframe)
    assert result["status"] == "PASS"
    assert result["total_records"] == 3
    assert result["valid_records"] == 3
    assert result["invalid_records"] == 0


def test_missing_key_row_is_invalid_and_warns():
    df = _frame(
        [
            _clean_row(),
            _clean_row(issue_id=None, issue_key=None, summary="No key at all"),
        ]
    )
    result = validate_dataframe(df)
    assert result["total_records"] == 2
    assert result["invalid_records"] == 1
    assert result["valid_records"] == 1
    assert result["missing_issue_id"] == 1
    assert result["missing_issue_key"] == 1
    assert result["status"] == "WARN"


def test_all_invalid_frame_fails():
    df = _frame(
        [
            _clean_row(issue_id=None, issue_key=None),
            _clean_row(issue_id=None, issue_key=None),
        ]
    )
    result = validate_dataframe(df)
    assert result["status"] == "FAIL"
    assert result["invalid_records"] == 2
    assert result["valid_records"] == 0


def test_duplicate_issue_id_is_counted():
    df = _frame([_clean_row(), _clean_row(issue_key="DATA-102")])
    result = validate_dataframe(df)
    assert result["duplicate_issue_id"] == 1
    assert result["status"] == "WARN"


def test_invalid_created_date_is_counted():
    df = _frame([_clean_row(created="not-a-date")])
    result = validate_dataframe(df)
    assert result["invalid_created_dates"] == 1


def test_empty_frame_passes_with_zero_total():
    df = pd.DataFrame(columns=SCHEMA_COLUMNS)
    result = validate_dataframe(df)
    assert result["status"] == "PASS"
    assert result["total_records"] == 0
    assert result["valid_records"] == 0
    assert result["invalid_records"] == 0


def test_drop_invalid_rows_drops_keyless_rows():
    df = _frame(
        [
            _clean_row(),
            _clean_row(issue_id=None, issue_key=None),
            _clean_row(issue_id="10003", issue_key="DATA-103"),
        ]
    )
    kept = drop_invalid_rows(df)
    assert len(kept) == 2
    assert list(kept["issue_key"]) == ["DATA-101", "DATA-103"]


def test_check_required_columns_lists_missing():
    df = pd.DataFrame({"issue_id": ["10001"], "issue_key": ["DATA-101"]})
    missing = check_required_columns(df)
    assert missing == ["summary", "created", "updated"]
    assert check_required_columns(df, required=["issue_id", "nope"]) == ["nope"]
