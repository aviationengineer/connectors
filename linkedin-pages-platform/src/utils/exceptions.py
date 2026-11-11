"""Custom exception hierarchy for the LinkedIn Pages ingestion framework.

Separating error types lets the retry logic and the pipeline distinguish
between transient failures (worth retrying) and permanent ones (never
worth retrying, e.g. bad credentials or a missing Community Management
product on the LinkedIn app).
"""

from __future__ import annotations


class LinkedInFrameworkError(Exception):
    """Base class for all framework errors."""


class ConfigError(LinkedInFrameworkError):
    """Raised when configuration is missing, malformed or incomplete."""


class LinkedInAPIError(LinkedInFrameworkError):
    """Base class for errors returned by the LinkedIn REST API."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class LinkedInAuthError(LinkedInAPIError):
    """401 / 403 — token missing, expired, or lacking the required scope.

    Never retried: retrying bad credentials cannot succeed. A 401 with
    an expired access token should be handled by refreshing the token
    (see ``scripts/linkedin_auth.py --refresh``), not by retrying.
    """


class LinkedInForbiddenError(LinkedInAPIError):
    """403 with an insufficient-permissions body — e.g. the LinkedIn app
    has not been approved for the Community Management API product, or
    the authenticated member is not an admin of the requested Page.

    Never retried.
    """


class LinkedInNotFoundError(LinkedInAPIError):
    """404 — the organization, post or endpoint does not exist."""


class LinkedInRateLimitError(LinkedInAPIError):
    """429 — LinkedIn is rate limiting the caller. Retried with backoff."""


class LinkedInServerError(LinkedInAPIError):
    """5xx — LinkedIn (or a proxy) failed. Retried with backoff."""


class LinkedInConnectionError(LinkedInAPIError):
    """Network-level failure: timeout, DNS, connection refused."""


class TransformationError(LinkedInFrameworkError):
    """Raised when a LinkedIn payload cannot be normalised to the schema."""


class DataQualityError(LinkedInFrameworkError):
    """Raised when a DataFrame fails validation and cannot proceed."""


class WriterError(LinkedInFrameworkError):
    """Base class for output-destination write failures."""


class CSVWriterError(WriterError):
    """CSV write / append / upsert failure."""


class GoogleSheetsError(WriterError):
    """Google Sheets authentication or API failure."""


class MetadataError(LinkedInFrameworkError):
    """Pipeline metadata could not be read or written."""
