"""Custom exception hierarchy for the Jira data ingestion framework.

Separating error types lets the retry logic and the pipeline distinguish
between transient failures (worth retrying) and permanent ones (never
worth retrying, e.g. bad credentials or an invalid JQL query).
"""

from __future__ import annotations


class JiraFrameworkError(Exception):
    """Base class for all framework errors."""


class ConfigError(JiraFrameworkError):
    """Raised when configuration is missing, malformed or incomplete."""


class JiraAPIError(JiraFrameworkError):
    """Base class for errors returned by the Jira REST API."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class JiraAuthError(JiraAPIError):
    """401 / 403 — credentials are missing, wrong or lack permission.

    Never retried: retrying bad credentials cannot succeed.
    """


class InvalidJQLError(JiraAPIError):
    """400 — the JQL query is invalid.

    Never retried: the same query will fail the same way every time.
    """


class JiraRateLimitError(JiraAPIError):
    """429 — Jira is rate limiting the caller. Retried with backoff."""


class JiraServerError(JiraAPIError):
    """5xx — Jira (or a proxy) failed. Retried with backoff."""


class JiraConnectionError(JiraAPIError):
    """Network-level failure: timeout, DNS, connection refused."""


class TransformationError(JiraFrameworkError):
    """Raised when a Jira payload cannot be normalised to the schema."""


class DataQualityError(JiraFrameworkError):
    """Raised when a DataFrame fails validation and cannot proceed."""


class WriterError(JiraFrameworkError):
    """Base class for output-destination write failures."""


class CSVWriterError(WriterError):
    """CSV write / append / upsert failure."""


class GoogleSheetsError(WriterError):
    """Google Sheets authentication or API failure."""


class MetadataError(JiraFrameworkError):
    """Pipeline metadata could not be read or written."""
