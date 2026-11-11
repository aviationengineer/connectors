"""Google Sheets output writer (spec §21).

Merges the standardised Jira DataFrame into one worksheet of a shared
spreadsheet. The merge rule follows the api_to_sheets_pipeline skill:
never replace a whole shared tab — :meth:`upsert` (the pipeline default)
updates rows in place and appends only new keys. :meth:`write` is the
explicit replace operation and clears the worksheet first.

Header safety: if the worksheet already has a header that differs from
the incoming columns, a warning is logged and the existing header is
kept; rows are then aligned to the existing header order. A header is
only written to an empty sheet, via append (never an update on an empty
range).

Change detection: a row counts as ``updated`` only when its content
actually differs from the stored row. ``ingestion_timestamp`` is excluded
from the comparison — it is restamped on every run, so including it
would mark every row as updated on every run. ``skipped`` is the number
of existing rows left untouched, i.e. ``len(existing) - updated``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from src.clients.google_sheets_client import GoogleSheetsClient
from src.transformers.jira_transformer import SCHEMA_COLUMNS
from src.utils.exceptions import GoogleSheetsError
from src.utils.logger import get_logger
from src.writers.base_writer import DataWriter

logger = get_logger("jira_pipeline.google_sheet_writer")

_CHUNK_SIZE = 100

# Columns restamped on every run even when the issue itself did not
# change; excluded from upsert change detection (see module docstring).
_VOLATILE_COLUMNS = frozenset({"ingestion_timestamp"})


def _column_letter(index: int) -> str:
    """1-based column number to spreadsheet letters (1 -> "A", 27 -> "AA")."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _a1_row_range(row_number: int, n_columns: int) -> str:
    """A1 range for one whole row, e.g. ``A2:E2``."""
    return f"A{row_number}:{_column_letter(n_columns)}{row_number}"


def _serialise_value(value: Any) -> str:
    """Serialise one cell: NaN/None -> "", datetimes -> ISO strings."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ";".join(str(item) for item in value if item is not None)
    if isinstance(value, dict):
        return str(value)
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


class GoogleSheetWriter(DataWriter):
    """Upsert Jira DataFrames into a Google Sheets worksheet.

    Args:
        spreadsheet_id: Target spreadsheet id.
        worksheet_name: Target worksheet (tab) title.
        credentials_file: Service-account JSON key; passed through to
            :class:`GoogleSheetsClient` when no client is injected.
        client: Optional injected client (dependency injection for tests
            and for sharing one authorised client across writers).
    """

    def __init__(
        self,
        spreadsheet_id: str,
        worksheet_name: str = "jira_issues",
        credentials_file: str | None = None,
        client: GoogleSheetsClient | None = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.worksheet_name = worksheet_name
        self.client = client or GoogleSheetsClient(credentials_file=credentials_file)
        self._worksheet: Any | None = None
        self._headers: list[str] = []

    # -- public API ---------------------------------------------------------
    def write(self, dataframe: pd.DataFrame) -> int:
        """Replace the worksheet content with ``dataframe``.

        Explicit replace operation: the worksheet is cleared, then the
        header and all rows are written in chunks. Returns rows written.
        """
        headers = [str(c) for c in dataframe.columns] or list(SCHEMA_COLUMNS)
        worksheet = self._ensure_worksheet(headers)
        try:
            worksheet.clear()
        except Exception as exc:
            raise GoogleSheetsError(
                f"Failed to clear worksheet {self.worksheet_name!r}: {exc}"
            ) from exc
        # After a clear the old header is gone, so the new header wins.
        self._headers = headers
        rows = [headers] + [
            self._serialise_row(row, headers)
            for _, row in dataframe.iterrows()
        ]
        self.client.append_rows(worksheet, rows)
        logger.info(
            "Wrote %d rows to worksheet %r", len(dataframe), self.worksheet_name
        )
        return len(dataframe)

    def append(self, dataframe: pd.DataFrame) -> dict:
        """Append only rows whose key is not already in the worksheet."""
        if dataframe.empty:
            return self._empty_counts()
        key_column = self._resolve_key_column(dataframe, "issue_id")
        headers = [str(c) for c in dataframe.columns]
        worksheet = self._ensure_worksheet(headers)
        _, existing_rows = self._read_existing(worksheet)
        key_index = self._key_index(key_column)
        existing_keys = (
            {row[key_index] for row in existing_rows}
            if key_index is not None
            else set()
        )
        incoming = dataframe.drop_duplicates(subset=[key_column], keep="last")
        new_rows: list[list[str]] = []
        skipped = 0
        for _, row in incoming.iterrows():
            serialised = self._serialise_row(row, self._headers)
            if key_index is not None and serialised[key_index] in existing_keys:
                skipped += 1
            else:
                new_rows.append(serialised)
        if new_rows:
            self._append_in_chunks(worksheet, new_rows)
        result = {
            "inserted": len(new_rows),
            "updated": 0,
            "skipped": skipped,
            "total": len(existing_rows) + len(new_rows),
        }
        logger.info(
            "Google Sheets append to %r: inserted=%d skipped=%d total=%d",
            self.worksheet_name,
            result["inserted"],
            skipped,
            result["total"],
        )
        return result

    def upsert(
        self, dataframe: pd.DataFrame, key_column: str = "issue_id"
    ) -> dict:
        """Merge ``dataframe`` into the worksheet on ``key_column``.

        Existing keys whose content differs are updated in place via
        ``update_range`` (flushed in chunks of 100 single-row updates);
        new keys are appended in chunks of 100. After writing, the row
        count is read back and logged. ``skipped`` is the number of
        existing rows left untouched; ``total`` is the final row count.
        """
        if dataframe.empty:
            return self._empty_counts()
        key_column = self._resolve_key_column(dataframe, key_column)
        headers = [str(c) for c in dataframe.columns]
        worksheet = self._ensure_worksheet(headers)
        _, existing_rows = self._read_existing(worksheet)
        key_index = self._key_index(key_column)
        compare_indexes = [
            index
            for index, header in enumerate(self._headers)
            if header not in _VOLATILE_COLUMNS
        ]
        # 1-based sheet row numbers; the header is row 1. First occurrence
        # wins when the sheet somehow holds a duplicate key.
        key_to_row: dict[str, int] = {}
        if key_index is not None:
            for position, row in enumerate(existing_rows):
                key_to_row.setdefault(row[key_index], position + 2)
        incoming = dataframe.drop_duplicates(subset=[key_column], keep="last")
        updates: list[tuple[int, list[str]]] = []
        new_rows: list[list[str]] = []
        updated = 0
        for _, row in incoming.iterrows():
            serialised = self._serialise_row(row, self._headers)
            key_value = serialised[key_index] if key_index is not None else None
            if key_index is not None and key_value in key_to_row:
                sheet_row = key_to_row[key_value]
                existing_row = existing_rows[sheet_row - 2]
                if any(
                    serialised[index] != existing_row[index]
                    for index in compare_indexes
                ):
                    updates.append((sheet_row, serialised))
                    updated += 1
                # Identical content: row left untouched.
            else:
                new_rows.append(serialised)
        for start in range(0, len(updates), _CHUNK_SIZE):
            for sheet_row, serialised in updates[start : start + _CHUNK_SIZE]:
                self.client.update_range(
                    worksheet,
                    _a1_row_range(sheet_row, len(self._headers)),
                    [serialised],
                )
        if new_rows:
            self._append_in_chunks(worksheet, new_rows)
        # Read back and verify the final row count before calling it done.
        _, final_rows = self._read_existing(worksheet)
        total = len(final_rows)
        result = {
            "inserted": len(new_rows),
            "updated": updated,
            "skipped": len(existing_rows) - updated,
            "total": total,
        }
        logger.info(
            "Google Sheets upsert to %r: inserted=%d updated=%d skipped=%d "
            "total=%d (read back)",
            self.worksheet_name,
            result["inserted"],
            updated,
            result["skipped"],
            total,
        )
        return result

    # -- helpers ------------------------------------------------------------
    def _ensure_worksheet(self, headers: list[str]) -> Any:
        """Open the spreadsheet/worksheet on first use; ensure the header.

        Returns the worksheet. On an empty sheet the header row is written
        (via append). When the sheet already has a different header, a
        warning is logged and the existing header is kept — a shared tab
        is never wiped. The effective header is stored on ``self._headers``.
        """
        headers = [str(header) for header in headers]
        if self._worksheet is not None and self._headers:
            return self._worksheet
        spreadsheet = self.client.open_spreadsheet(self.spreadsheet_id)
        worksheet = self.client.get_or_create_worksheet(
            spreadsheet, self.worksheet_name
        )
        values = self.client.read_all_values(worksheet)
        existing_header = self._clean_header(values[0] if values else [])
        if existing_header:
            if headers and existing_header != headers:
                logger.warning(
                    "Worksheet %r already has header %s, which differs "
                    "from the incoming header %s; keeping the existing "
                    "header and aligning rows to it",
                    self.worksheet_name,
                    existing_header,
                    headers,
                )
            self._headers = existing_header
        elif headers:
            self.client.append_rows(worksheet, [headers])
            self._headers = headers
        else:
            self._headers = []
        self._worksheet = worksheet
        return worksheet

    def _read_existing(self, worksheet: Any) -> tuple[list[str], list[list[str]]]:
        """Return ``(header, data rows)`` with rows padded to header width."""
        values = self.client.read_all_values(worksheet)
        if not values:
            return [], []
        header = self._clean_header(values[0])
        rows: list[list[str]] = []
        for row in values[1:]:
            cells = [str(cell) for cell in row]
            if len(cells) < len(header):
                cells += [""] * (len(header) - len(cells))
            rows.append(cells[: len(header)])
        return header, rows

    @staticmethod
    def _clean_header(header: list[Any]) -> list[str]:
        """Header cells as strings with trailing empties stripped."""
        cells = [str(cell) for cell in header]
        while cells and cells[-1] == "":
            cells.pop()
        return cells

    def _empty_counts(self) -> dict:
        """No-op counts for an empty frame, with the existing row total."""
        worksheet = self._ensure_worksheet([])
        _, existing_rows = self._read_existing(worksheet)
        total = len(existing_rows)
        return {"inserted": 0, "updated": 0, "skipped": total, "total": total}

    def _key_index(self, key_column: str) -> int | None:
        """Position of the key column in the effective header, or None."""
        if key_column in self._headers:
            return self._headers.index(key_column)
        logger.warning(
            "Key column %r not in worksheet %r header; rows cannot be "
            "matched against existing rows and will all be treated as new",
            key_column,
            self.worksheet_name,
        )
        return None

    @staticmethod
    def _resolve_key_column(dataframe: pd.DataFrame, key_column: str) -> str:
        """Return the key column, falling back to ``issue_key``."""
        if key_column in dataframe.columns:
            return key_column
        if "issue_key" in dataframe.columns:
            logger.warning(
                "Key column %r not in DataFrame; falling back to 'issue_key'",
                key_column,
            )
            return "issue_key"
        raise GoogleSheetsError(
            f"DataFrame has neither {key_column!r} nor 'issue_key' column; "
            "cannot upsert/append by key"
        )

    def _serialise_row(self, row: pd.Series, headers: list[str]) -> list[str]:
        """Serialise one DataFrame row as strings in ``headers`` order."""
        return [
            _serialise_value(row[header]) if header in row.index else ""
            for header in headers
        ]

    def _append_in_chunks(
        self, worksheet: Any, rows: list[list[str]]
    ) -> None:
        """Append ``rows`` via the client in chunks of at most 100."""
        for start in range(0, len(rows), _CHUNK_SIZE):
            self.client.append_rows(worksheet, rows[start : start + _CHUNK_SIZE])
