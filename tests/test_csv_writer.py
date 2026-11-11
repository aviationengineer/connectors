"""Tests for CSVWriter — no live Jira, no live Google."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.transformers.jira_transformer import SCHEMA_COLUMNS
from src.utils.exceptions import CSVWriterError
from src.writers.csv_writer import CSVWriter

COLUMNS = ["issue_id", "issue_key", "summary", "status", "updated"]


def make_frame(rows: list[dict] | None = None) -> pd.DataFrame:
    """Small Jira-shaped frame (3 rows by default)."""
    data = rows if rows is not None else [
        {
            "issue_id": "1001",
            "issue_key": "DATA-1",
            "summary": "First issue",
            "status": "Done",
            "updated": "2026-11-09T10:00:00Z",
        },
        {
            "issue_id": "1002",
            "issue_key": "DATA-2",
            "summary": "Second issue",
            "status": "In Progress",
            "updated": "2026-11-09T11:00:00Z",
        },
        {
            "issue_id": "1003",
            "issue_key": "DATA-3",
            "summary": "Third issue",
            "status": "To Do",
            "updated": "2026-11-09T12:00:00Z",
        },
    ]
    return pd.DataFrame(data, columns=COLUMNS)


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str)


def test_write_creates_file_with_header(tmp_path: Path):
    path = tmp_path / "issues.csv"
    assert CSVWriter().write(make_frame(), path) == 3
    assert path.is_file()
    df = read_csv(path)
    assert list(df.columns) == COLUMNS
    assert len(df) == 3
    assert set(df["issue_key"]) == {"DATA-1", "DATA-2", "DATA-3"}


def test_write_empty_dataframe_writes_schema_header(tmp_path: Path):
    path = tmp_path / "issues.csv"
    assert CSVWriter().write(pd.DataFrame(), path) == 0
    assert path.read_text(encoding="utf-8").splitlines()[0].split(",")[0] == "issue_id"
    assert read_csv(path).columns.tolist() == SCHEMA_COLUMNS


def test_append_adds_only_new_keys(tmp_path: Path):
    writer = CSVWriter()
    path = tmp_path / "issues.csv"
    writer.write(make_frame(), path)
    incoming = make_frame(
        [
            {
                "issue_id": "1002",
                "issue_key": "DATA-2",
                "summary": "Second issue",
                "status": "In Progress",
                "updated": "2026-11-09T11:00:00Z",
            },
            {
                "issue_id": "1004",
                "issue_key": "DATA-4",
                "summary": "Fourth issue",
                "status": "To Do",
                "updated": "2026-11-10T09:00:00Z",
            },
            {
                "issue_id": "1005",
                "issue_key": "DATA-5",
                "summary": "Fifth issue",
                "status": "To Do",
                "updated": "2026-11-10T09:30:00Z",
            },
        ]
    )
    result = writer.append(incoming, path)
    assert result == {"inserted": 2, "updated": 0, "skipped": 1, "total": 5}
    df = read_csv(path)
    assert len(df) == 5
    assert df["issue_key"].is_unique


def test_append_to_missing_file_behaves_like_write(tmp_path: Path):
    path = tmp_path / "issues.csv"
    result = CSVWriter().append(make_frame(), path)
    assert result == {"inserted": 3, "updated": 0, "skipped": 0, "total": 3}
    assert len(read_csv(path)) == 3


def test_upsert_inserts_new_updates_changed_no_duplicates(tmp_path: Path):
    writer = CSVWriter()
    path = tmp_path / "issues.csv"
    first = writer.upsert(make_frame(), path)
    assert first == {"inserted": 3, "updated": 0, "skipped": 0, "total": 3}

    changed = make_frame(
        [
            {
                "issue_id": "1001",
                "issue_key": "DATA-1",
                "summary": "First issue",
                "status": "Done",
                "updated": "2026-11-09T10:00:00Z",
            },
            {
                "issue_id": "1002",
                "issue_key": "DATA-2",
                "summary": "Second issue UPDATED",
                "status": "Done",
                "updated": "2026-11-10T09:00:00Z",
            },
            {
                "issue_id": "1003",
                "issue_key": "DATA-3",
                "summary": "Third issue",
                "status": "To Do",
                "updated": "2026-11-09T12:00:00Z",
            },
            {
                "issue_id": "1004",
                "issue_key": "DATA-4",
                "summary": "Fourth issue",
                "status": "To Do",
                "updated": "2026-11-10T09:30:00Z",
            },
        ]
    )
    result = writer.upsert(changed, path)
    assert result == {"inserted": 1, "updated": 1, "skipped": 2, "total": 4}
    df = read_csv(path)
    assert len(df) == 4
    assert df["issue_key"].is_unique
    assert (
        df.loc[df["issue_key"] == "DATA-2", "summary"].iloc[0]
        == "Second issue UPDATED"
    )

    # Running the same upsert again changes nothing: no duplicates,
    # row count stable, updated value still present.
    again = writer.upsert(changed, path)
    assert again == {"inserted": 0, "updated": 0, "skipped": 4, "total": 4}
    df = read_csv(path)
    assert len(df) == 4
    assert (
        df.loc[df["issue_key"] == "DATA-2", "summary"].iloc[0]
        == "Second issue UPDATED"
    )


def test_key_fallback_to_issue_key(tmp_path: Path):
    writer = CSVWriter()
    path = tmp_path / "issues.csv"
    frame = make_frame().drop(columns=["issue_id"])
    assert writer.write(frame, path) == 3

    incoming = pd.DataFrame(
        [
            {
                "issue_key": "DATA-2",
                "summary": "Second issue",
                "status": "In Progress",
                "updated": "2026-11-09T11:00:00Z",
            },
            {
                "issue_key": "DATA-4",
                "summary": "Fourth issue",
                "status": "To Do",
                "updated": "2026-11-10T09:00:00Z",
            },
        ],
        columns=["issue_key", "summary", "status", "updated"],
    )
    result = writer.append(incoming, path)
    assert result["inserted"] == 1
    assert result["skipped"] == 1
    assert result["total"] == 4

    changed = pd.DataFrame(
        [
            {
                "issue_key": "DATA-2",
                "summary": "Second issue UPDATED",
                "status": "Done",
                "updated": "2026-11-10T09:00:00Z",
            }
        ],
        columns=["issue_key", "summary", "status", "updated"],
    )
    result = writer.upsert(changed, path)
    assert result == {"inserted": 0, "updated": 1, "skipped": 3, "total": 4}
    df = read_csv(path)
    assert (
        df.loc[df["issue_key"] == "DATA-2", "summary"].iloc[0]
        == "Second issue UPDATED"
    )


def test_empty_dataframe_noop(tmp_path: Path):
    writer = CSVWriter()
    path = tmp_path / "issues.csv"
    writer.write(make_frame(), path)
    empty = pd.DataFrame(columns=COLUMNS)
    assert writer.append(empty, path) == {
        "inserted": 0,
        "updated": 0,
        "skipped": 3,
        "total": 3,
    }
    assert writer.upsert(empty, path) == {
        "inserted": 0,
        "updated": 0,
        "skipped": 3,
        "total": 3,
    }
    assert len(read_csv(path)) == 3

    missing = tmp_path / "missing.csv"
    assert writer.append(empty, missing) == {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "total": 0,
    }
    assert not missing.exists()


def test_corrupt_existing_csv_is_backed_up_and_fresh_data_written(tmp_path: Path):
    writer = CSVWriter()
    path = tmp_path / "issues.csv"
    # Unclosed quote: pandas raises ParserError on read.
    path.write_text('issue_id,issue_key\n"1001,DATA-1\n', encoding="utf-8")
    result = writer.upsert(make_frame(), path)
    assert result == {"inserted": 3, "updated": 0, "skipped": 0, "total": 3}
    backups = list(tmp_path.glob("issues.csv.corrupt-*"))
    assert len(backups) == 1
    assert "1001" in backups[0].read_text(encoding="utf-8")
    assert len(read_csv(path)) == 3


def test_missing_key_columns_raises(tmp_path: Path):
    writer = CSVWriter()
    path = tmp_path / "issues.csv"
    frame = pd.DataFrame(
        [{"summary": "No keys here", "status": "To Do"}],
        columns=["summary", "status"],
    )
    with pytest.raises(CSVWriterError):
        writer.append(frame, path)
    with pytest.raises(CSVWriterError):
        writer.upsert(frame, path)
