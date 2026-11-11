"""Tests for data-quality validation of the canonical post DataFrame."""

from __future__ import annotations

import pandas as pd

from src.transformers.linkedin_transformer import SCHEMA_COLUMNS
from src.utils import data_quality


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    return frame.reindex(
        columns=SCHEMA_COLUMNS + [c for c in frame.columns if c not in SCHEMA_COLUMNS]
    )


def _row(**overrides) -> dict:
    row = {
        "post_urn": "urn:li:share:111",
        "post_url": "https://www.linkedin.com/feed/update/urn:li:share:111",
        "organization_urn": "urn:li:organization:1",
        "organization_id": "1",
        "organization_name": "Acme Corp",
        "author_type": "organization",
        "text": "Hello",
        "created_at": "2026-11-11T00:00:00Z",
        "visibility": "PUBLIC",
        "lifecycle_state": "PUBLISHED",
        "likes_count": 1,
        "comments_count": 0,
        "shares_count": 0,
        "media_type": "",
        "media_title": "",
        "media_url": "",
        "article_url": "",
        "source": "LINKEDIN",
        "extraction_timestamp": "2026-11-11T00:00:00Z",
        "ingestion_timestamp": "2026-11-11T00:00:00Z",
    }
    row.update(overrides)
    return row


def test_valid_frame_passes() -> None:
    summary = data_quality.validate_dataframe(_frame([_row()]))
    assert summary["status"] == "PASS"
    assert summary["total_records"] == 1
    assert summary["valid_records"] == 1
    assert summary["invalid_records"] == 0


def test_empty_frame_passes() -> None:
    summary = data_quality.validate_dataframe(pd.DataFrame(columns=SCHEMA_COLUMNS))
    assert summary["status"] == "PASS"
    assert summary["total_records"] == 0


def test_missing_business_key_warns() -> None:
    frame = _frame([_row(), _row(post_urn="")])
    summary = data_quality.validate_dataframe(frame)
    assert summary["status"] == "WARN"
    assert summary["invalid_records"] == 1
    assert summary["valid_records"] == 1


def test_all_rows_invalid_fails() -> None:
    frame = _frame([_row(post_urn=""), _row(post_urn=None)])
    summary = data_quality.validate_dataframe(frame)
    assert summary["status"] == "FAIL"


def test_duplicate_business_key_warns() -> None:
    frame = _frame([_row(), _row()])
    summary = data_quality.validate_dataframe(frame)
    assert summary["status"] == "WARN"
    assert summary["duplicate_business_key"] == 1


def test_invalid_url_flagged() -> None:
    frame = _frame([_row(post_url="not a url")])
    summary = data_quality.validate_dataframe(frame)
    assert summary["invalid_urls"] == 1
    assert summary["status"] == "WARN"


def test_invalid_date_flagged() -> None:
    frame = _frame([_row(created_at="yesterday-ish")])
    summary = data_quality.validate_dataframe(frame)
    assert summary["invalid_dates"] == 1
    assert summary["status"] == "WARN"


def test_null_percentage_reported() -> None:
    frame = _frame([_row(), _row(media_title=None, article_url=None)])
    summary = data_quality.validate_dataframe(frame)
    assert summary["null_percentage"]["media_title"] == 50.0


def test_check_required_columns() -> None:
    frame = pd.DataFrame([{"post_urn": "x"}])
    assert data_quality.check_required_columns(frame) == [
        "organization_urn",
        "created_at",
    ]


def test_drop_invalid_rows() -> None:
    frame = _frame([_row(), _row(post_urn="")])
    kept = data_quality.drop_invalid_rows(frame)
    assert len(kept) == 1
