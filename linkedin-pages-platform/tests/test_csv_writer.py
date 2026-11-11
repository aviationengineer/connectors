"""Tests for the CSV writer (upsert / append / corrupt-file handling)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.transformers.linkedin_transformer import SCHEMA_COLUMNS
from src.writers.csv_writer import CSVWriter


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    return frame.reindex(
        columns=SCHEMA_COLUMNS + [c for c in frame.columns if c not in SCHEMA_COLUMNS]
    )


@pytest.fixture()
def writer() -> CSVWriter:
    return CSVWriter()


@pytest.fixture()
def rows() -> list[dict]:
    return [
        {
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
    ]


def test_write_creates_file(writer: CSVWriter, tmp_path: Path, rows) -> None:
    path = tmp_path / "posts.csv"
    assert writer.write(_frame(rows), path) == 1
    assert pd.read_csv(path).iloc[0]["post_urn"] == "urn:li:share:111"


def test_write_empty_frame_writes_schema_header(writer: CSVWriter, tmp_path: Path) -> None:
    path = tmp_path / "posts.csv"
    assert writer.write(pd.DataFrame(), path) == 0
    assert list(pd.read_csv(path, nrows=0).columns) == SCHEMA_COLUMNS


def test_append_deduplicates(writer: CSVWriter, tmp_path: Path, rows) -> None:
    path = tmp_path / "posts.csv"
    writer.write(_frame(rows), path)
    result = writer.append(_frame(rows), path, key_column="post_urn")
    assert result == {"inserted": 0, "updated": 0, "skipped": 1, "total": 1}


def test_upsert_second_run_skips_identical(writer: CSVWriter, tmp_path: Path, rows) -> None:
    path = tmp_path / "posts.csv"
    first = writer.upsert(_frame(rows), path, key_column="post_urn")
    assert first["inserted"] == 1
    second = writer.upsert(_frame(rows), path, key_column="post_urn")
    assert second == {"inserted": 0, "updated": 0, "skipped": 1, "total": 1}


def test_upsert_updates_changed_row(writer: CSVWriter, tmp_path: Path, rows) -> None:
    path = tmp_path / "posts.csv"
    writer.upsert(_frame(rows), path, key_column="post_urn")
    changed = [dict(rows[0], text="Hello, edited", likes_count=5)]
    result = writer.upsert(_frame(changed), path, key_column="post_urn")
    assert result["updated"] == 1
    assert result["inserted"] == 0
    assert pd.read_csv(path).iloc[0]["text"] == "Hello, edited"


def test_upsert_ingestion_timestamp_change_alone_is_not_an_update(
    writer: CSVWriter, tmp_path: Path, rows
) -> None:
    path = tmp_path / "posts.csv"
    writer.upsert(_frame(rows), path, key_column="post_urn")
    restamped = [dict(rows[0], ingestion_timestamp="2026-11-12T00:00:00Z")]
    result = writer.upsert(_frame(restamped), path, key_column="post_urn")
    assert result["updated"] == 0
    assert result["skipped"] == 1


def test_upsert_inserts_new_and_keeps_old(writer: CSVWriter, tmp_path: Path, rows) -> None:
    path = tmp_path / "posts.csv"
    writer.upsert(_frame(rows), path, key_column="post_urn")
    extra = dict(rows[0], post_urn="urn:li:share:222",
                 post_url="https://www.linkedin.com/feed/update/urn:li:share:222",
                 text="Second post")
    result = writer.upsert(_frame(rows + [extra]), path, key_column="post_urn")
    assert result["inserted"] == 1
    assert result["total"] == 2


def test_corrupt_csv_moved_aside(writer: CSVWriter, tmp_path: Path, rows) -> None:
    path = tmp_path / "posts.csv"
    path.write_text("not,a,valid\n\"unclosed quote", encoding="utf-8")
    result = writer.upsert(_frame(rows), path, key_column="post_urn")
    assert result["inserted"] == 1
    backups = list(tmp_path.glob("posts.csv.corrupt-*"))
    assert len(backups) == 1
