"""Tests for GoogleSheetWriter — mocked gspread, no live Google.

The fakes mirror the ones in tests/test_acceptance.py: a worksheet that
stores rows in memory, a spreadsheet holding named worksheets, and a
client that passes calls straight through (no internal chunking, so the
writer's own chunking is what the tests measure).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from src.writers.google_sheet_writer import GoogleSheetWriter

COLUMNS = ["issue_id", "issue_key", "summary", "status", "updated"]
HEADER = COLUMNS


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


def serialised(row: dict) -> list[str]:
    return [str(row[column]) for column in COLUMNS]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeWorksheet:
    """In-memory worksheet storing rows of strings."""

    def __init__(self, title: str = "jira_issues") -> None:
        self.title = title
        self.rows: list[list[str]] = []
        self.cleared = False

    def get_all_values(self) -> list[list[str]]:
        return [list(row) for row in self.rows]

    def append_rows(self, rows, value_input_option=None) -> None:
        self.rows.extend([list(row) for row in rows])

    def update(self, range_name, values, value_input_option=None) -> None:
        row_no = int(re.search(r"(\d+)", range_name).group(1))
        while len(self.rows) < row_no:
            self.rows.append([])
        for offset, row in enumerate(values):
            self.rows[row_no - 1 + offset] = list(row)

    def clear(self) -> None:
        self.rows = []
        self.cleared = True


class FakeSpreadsheet:
    def __init__(self) -> None:
        self._sheets: dict[str, FakeWorksheet] = {}

    def worksheet(self, title: str) -> FakeWorksheet:
        if title not in self._sheets:
            raise KeyError(title)
        return self._sheets[title]

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        sheet = FakeWorksheet(title)
        self._sheets[title] = sheet
        return sheet


class FakeClient:
    """Stand-in for GoogleSheetsClient; records every call."""

    def __init__(self) -> None:
        self.spreadsheet = FakeSpreadsheet()
        self.append_calls: list[list[list[str]]] = []
        self.update_calls: list[tuple[str, list[list[str]]]] = []

    def authorize(self):
        return self

    def open_spreadsheet(self, spreadsheet_id: str) -> FakeSpreadsheet:
        return self.spreadsheet

    def get_or_create_worksheet(
        self, spreadsheet, title: str, rows: int = 1000, cols: int = 40
    ) -> FakeWorksheet:
        try:
            return spreadsheet.worksheet(title)
        except KeyError:
            return spreadsheet.add_worksheet(title, rows, cols)

    def read_all_values(self, worksheet: FakeWorksheet) -> list[list[str]]:
        return worksheet.get_all_values()

    def append_rows(
        self, worksheet: FakeWorksheet, rows, chunk_size: int = 100
    ) -> int:
        rows = list(rows)
        self.append_calls.append([list(row) for row in rows])
        worksheet.append_rows(rows)
        return len(rows)

    def update_range(
        self, worksheet: FakeWorksheet, range_name: str, values
    ) -> None:
        self.update_calls.append((range_name, [list(row) for row in values]))
        worksheet.update(range_name, values)


def make_writer(client: FakeClient) -> GoogleSheetWriter:
    return GoogleSheetWriter(
        spreadsheet_id="fake-spreadsheet-id",
        worksheet_name="jira_issues",
        client=client,
    )


def seed(client: FakeClient, rows: list[list[str]]) -> FakeWorksheet:
    """Pre-fill the worksheet (header + data rows)."""
    sheet = client.spreadsheet.add_worksheet("jira_issues", 1000, 40)
    sheet.rows = [list(row) for row in rows]
    return sheet


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_upsert_inserts_new_and_updates_existing_in_place():
    client = FakeClient()
    sheet = seed(
        client,
        [
            HEADER,
            ["1001", "DATA-1", "First issue", "Done", "2026-11-09T10:00:00Z"],
            ["1002", "DATA-2", "Second issue", "In Progress", "2026-11-09T11:00:00Z"],
        ],
    )
    incoming = make_frame(
        [
            {
                "issue_id": "1001",
                "issue_key": "DATA-1",
                "summary": "First issue UPDATED",
                "status": "Done",
                "updated": "2026-11-10T09:00:00Z",
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
    )
    result = make_writer(client).upsert(incoming)

    assert result == {"inserted": 1, "updated": 1, "skipped": 1, "total": 3}
    # DATA-1 updated in place on sheet row 2; DATA-2 untouched.
    assert ("A2:E2", [["1001", "DATA-1", "First issue UPDATED", "Done", "2026-11-10T09:00:00Z"]]) in client.update_calls
    assert len(client.update_calls) == 1
    # Only the new key was appended.
    appended = [row for call in client.append_calls for row in call]
    assert appended == [["1003", "DATA-3", "Third issue", "To Do", "2026-11-09T12:00:00Z"]]
    assert len(sheet.rows) == 4  # header + 3 rows, no duplicates
    assert sheet.rows[1][2] == "First issue UPDATED"
    assert sheet.rows[2][2] == "Second issue"


def test_append_skips_existing_keys():
    client = FakeClient()
    sheet = seed(
        client,
        [
            HEADER,
            ["1001", "DATA-1", "First issue", "Done", "2026-11-09T10:00:00Z"],
            ["1002", "DATA-2", "Second issue", "In Progress", "2026-11-09T11:00:00Z"],
        ],
    )
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
                "issue_id": "1003",
                "issue_key": "DATA-3",
                "summary": "Third issue",
                "status": "To Do",
                "updated": "2026-11-09T12:00:00Z",
            },
        ]
    )
    result = make_writer(client).append(incoming)
    assert result == {"inserted": 1, "updated": 0, "skipped": 1, "total": 3}
    assert len(sheet.rows) == 4
    assert client.update_calls == []


def test_write_replaces_content():
    client = FakeClient()
    sheet = seed(
        client,
        [
            HEADER,
            ["1001", "DATA-1", "First issue", "Done", "2026-11-09T10:00:00Z"],
            ["1002", "DATA-2", "Second issue", "In Progress", "2026-11-09T11:00:00Z"],
        ],
    )
    incoming = make_frame(
        [
            {
                "issue_id": "1009",
                "issue_key": "DATA-9",
                "summary": "Ninth issue",
                "status": "To Do",
                "updated": "2026-11-10T09:00:00Z",
            },
            {
                "issue_id": "1010",
                "issue_key": "DATA-10",
                "summary": "Tenth issue",
                "status": "To Do",
                "updated": "2026-11-10T09:30:00Z",
            },
        ]
    )
    assert make_writer(client).write(incoming) == 2
    assert sheet.cleared is True
    assert sheet.rows[0] == HEADER
    assert len(sheet.rows) == 3
    assert {row[1] for row in sheet.rows[1:]} == {"DATA-9", "DATA-10"}


def test_header_created_on_empty_sheet():
    client = FakeClient()
    writer = make_writer(client)
    result = writer.upsert(make_frame().head(2))
    assert result == {"inserted": 2, "updated": 0, "skipped": 0, "total": 2}
    sheet = client.spreadsheet.worksheet("jira_issues")
    assert sheet.rows[0] == HEADER
    assert len(sheet.rows) == 3


def test_existing_different_header_logs_warning_and_is_kept(caplog):
    client = FakeClient()
    sheet = seed(
        client,
        [
            ["issue_id", "issue_key", "custom"],
            ["1001", "DATA-1", "old"],
        ],
    )
    with caplog.at_level(logging.WARNING, logger="jira_pipeline.google_sheet_writer"):
        result = make_writer(client).upsert(make_frame().head(1))
    assert "differs from the incoming header" in caplog.text
    assert result["updated"] == 1
    assert result["total"] == 1
    # Existing header kept, not replaced by the incoming columns; the row
    # is aligned to it, so "summary" (not in the kept header) is dropped
    # and "custom" (not in the frame) becomes "".
    assert sheet.rows[0] == ["issue_id", "issue_key", "custom"]
    assert sheet.rows[1] == ["1001", "DATA-1", ""]


def test_chunking_250_rows_appended_in_three_data_calls():
    client = FakeClient()
    writer = make_writer(client)
    rows = [
        {
            "issue_id": str(1000 + n),
            "issue_key": f"DATA-{n}",
            "summary": f"Issue {n}",
            "status": "To Do",
            "updated": "2026-11-10T09:00:00Z",
        }
        for n in range(1, 251)
    ]
    result = writer.append(make_frame(rows))
    assert result == {"inserted": 250, "updated": 0, "skipped": 0, "total": 250}
    # One header call, then data in chunks of 100 / 100 / 50.
    assert [len(call) for call in client.append_calls] == [1, 100, 100, 50]
    sheet = client.spreadsheet.worksheet("jira_issues")
    assert len(sheet.rows) == 251


def test_empty_dataframe_noop():
    client = FakeClient()
    sheet = seed(
        client,
        [
            HEADER,
            ["1001", "DATA-1", "First issue", "Done", "2026-11-09T10:00:00Z"],
            ["1002", "DATA-2", "Second issue", "In Progress", "2026-11-09T11:00:00Z"],
        ],
    )
    writer = make_writer(client)
    empty = pd.DataFrame(columns=COLUMNS)
    assert writer.append(empty) == {
        "inserted": 0,
        "updated": 0,
        "skipped": 2,
        "total": 2,
    }
    assert writer.upsert(empty) == {
        "inserted": 0,
        "updated": 0,
        "skipped": 2,
        "total": 2,
    }
    assert client.append_calls == []
    assert client.update_calls == []
    assert len(sheet.rows) == 3


def test_upsert_key_fallback_to_issue_key():
    client = FakeClient()
    sheet = seed(
        client,
        [
            ["issue_key", "summary", "status", "updated"],
            ["DATA-1", "First issue", "Done", "2026-11-09T10:00:00Z"],
        ],
    )
    frame = make_frame().drop(columns=["issue_id"]).head(1)
    frame = frame.assign(summary="First issue UPDATED")
    result = make_writer(client).upsert(frame)
    assert result == {"inserted": 0, "updated": 1, "skipped": 0, "total": 1}
    assert sheet.rows[1] == [
        "DATA-1",
        "First issue UPDATED",
        "Done",
        "2026-11-09T10:00:00Z",
    ]
