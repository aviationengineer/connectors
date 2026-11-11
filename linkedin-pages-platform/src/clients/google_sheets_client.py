"""Google Sheets API client (spec §21).

A thin wrapper around :mod:`gspread` that owns authentication and the
raw worksheet calls — nothing else. It knows nothing about DataFrames;
the writers own serialisation and merge logic.

Notes
-----
* ``gspread`` / ``google-auth`` are imported lazily inside
  :meth:`authorize` (and where the ``WorksheetNotFound`` exception is
  needed), so CSV-only users never need the libraries installed.
* Writes are chunked (max 100 rows per API call) with a brief pause
  between chunks, per the api_to_sheets_pipeline skill.
* Transient Sheets 429s are retried with exponential backoff via
  tenacity (4 attempts, multiplier 1, min 1s, max 30s).
* Every Sheets failure is wrapped in :class:`GoogleSheetsError`.
"""

from __future__ import annotations

import os
import time
from typing import Any

import tenacity

from src.utils.exceptions import GoogleSheetsError
from src.utils.logger import get_logger

logger = get_logger("linkedin_pipeline.google_sheets_client")

DEFAULT_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_CHUNK_SLEEP_SECONDS = 0.2
_MAX_ROWS_PER_CALL = 100


def _is_rate_limit_error(exc: BaseException) -> bool:
    """True when an exception looks like a Sheets 429 / quota error."""
    error = getattr(exc, "error", None)
    if isinstance(error, dict):
        code = error.get("code")
        if code == 429 or str(code) == "429":
            return True
        details = error.get("errors") or []
        if any(
            isinstance(item, dict)
            and item.get("reason") in {"rateLimitExceeded", "quotaExceeded"}
            for item in details
        ):
            return True
    text = str(exc).lower()
    return (
        "429" in text
        or "rate limit" in text
        or "quota exceeded" in text
        or "resource_exhausted" in text
    )


class GoogleSheetsClient:
    """Authenticated gspread client for one service-account credential.

    Args:
        credentials_file: Path to the service-account JSON key. May also
            come from the ``GOOGLE_APPLICATION_CREDENTIALS`` environment
            variable. Missing credentials are only an error when
            :meth:`authorize` is called, not at construction.
        scopes: OAuth scopes; defaults to Sheets + read-only Drive.
    """

    def __init__(
        self,
        credentials_file: str | None = None,
        scopes: list[str] | None = None,
    ) -> None:
        self.credentials_file = credentials_file or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS"
        )
        self.scopes = list(scopes) if scopes else list(DEFAULT_SCOPES)
        self._client: Any | None = None

    # -- authentication -----------------------------------------------------
    def authorize(self) -> Any:
        """Return the authorised gspread client, creating it on first use."""
        if self._client is not None:
            return self._client
        try:
            import gspread
            from google.oauth2 import service_account
        except ImportError as exc:
            raise GoogleSheetsError(
                "gspread and google-auth are required for Google Sheets "
                "output; install them with: pip install gspread google-auth"
            ) from exc
        if not self.credentials_file:
            raise GoogleSheetsError(
                "No Google service-account credentials file configured: "
                "pass credentials_file to GoogleSheetsClient or set the "
                "GOOGLE_APPLICATION_CREDENTIALS environment variable."
            )
        if not os.path.isfile(self.credentials_file):
            raise GoogleSheetsError(
                "Google credentials file not found: "
                f"{self.credentials_file}"
            )
        try:
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_file, scopes=self.scopes
            )
            self._client = gspread.authorize(credentials)
        except Exception as exc:
            raise GoogleSheetsError(
                f"Failed to authorise Google Sheets client: {exc}"
            ) from exc
        logger.info("Authorised Google Sheets client")
        return self._client

    # -- spreadsheet / worksheet access --------------------------------------
    def open_spreadsheet(self, spreadsheet_id: str) -> Any:
        """Open a spreadsheet by id (raises GoogleSheetsError on failure)."""
        client = self.authorize()
        try:
            return client.open_by_key(spreadsheet_id)
        except Exception as exc:
            raise GoogleSheetsError(
                f"Failed to open spreadsheet {spreadsheet_id}: {exc}"
            ) from exc

    def get_or_create_worksheet(
        self,
        spreadsheet: Any,
        title: str,
        rows: int = 1000,
        cols: int = 40,
    ) -> Any:
        """Return the worksheet named ``title``, creating it if needed."""
        try:
            import gspread
        except ImportError as exc:
            raise GoogleSheetsError(
                "gspread is required for Google Sheets output; install it "
                "with: pip install gspread"
            ) from exc
        try:
            return spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            try:
                worksheet = spreadsheet.add_worksheet(
                    title=title, rows=rows, cols=cols
                )
            except Exception as exc:
                raise GoogleSheetsError(
                    f"Failed to create worksheet {title!r}: {exc}"
                ) from exc
            logger.info("Created worksheet %r", title)
            return worksheet
        except Exception as exc:
            raise GoogleSheetsError(
                f"Failed to read worksheet {title!r}: {exc}"
            ) from exc

    def read_all_values(self, worksheet: Any) -> list[list[str]]:
        """Return every cell of ``worksheet`` as rows of strings."""
        try:
            return worksheet.get_all_values()
        except Exception as exc:
            raise GoogleSheetsError(
                f"Failed to read worksheet values: {exc}"
            ) from exc

    # -- writes ---------------------------------------------------------------
    def append_rows(
        self,
        worksheet: Any,
        rows: list[list[str]],
        chunk_size: int = 100,
    ) -> int:
        """Append ``rows`` in chunks of at most ``chunk_size``.

        Transient 429s are retried with exponential backoff (4 attempts).
        Returns the total number of rows appended.
        """
        rows = list(rows)
        if not rows:
            return 0
        total = 0
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            self._append_chunk(worksheet, chunk)
            total += len(chunk)
            if start + chunk_size < len(rows):
                time.sleep(_CHUNK_SLEEP_SECONDS)
        return total

    def _append_chunk(self, worksheet: Any, chunk: list[list[str]]) -> None:
        """Append one chunk, retrying transient rate-limit errors."""
        # Built per call so tests can monkeypatch tenacity.wait_exponential.
        retrying = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(4),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=30),
            retry=tenacity.retry_if_exception(_is_rate_limit_error),
            reraise=True,
        )
        try:
            retrying(
                lambda: worksheet.append_rows(
                    chunk, value_input_option="USER_ENTERED"
                )
            )
        except Exception as exc:
            raise GoogleSheetsError(
                f"Failed to append {len(chunk)} rows to Google Sheet: {exc}"
            ) from exc

    def update_range(
        self,
        worksheet: Any,
        range_name: str,
        values: list[list[str]],
    ) -> None:
        """Write ``values`` into the A1 ``range_name`` of ``worksheet``.

        Note: gspread 6.x takes values first
        (``worksheet.update(values, range_name=...)``), so the call below
        passes the range as a keyword, not positionally.
        """
        try:
            worksheet.update(
                values,
                range_name=range_name,
                value_input_option="USER_ENTERED",
            )
        except Exception as exc:
            raise GoogleSheetsError(
                f"Failed to update range {range_name}: {exc}"
            ) from exc
