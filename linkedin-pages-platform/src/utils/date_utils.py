"""Date and time helpers.

LinkedIn's versioned REST API returns timestamps as epoch milliseconds
(e.g. a post's ``publishedAt`` / ``createdAt``), while the pipeline's
canonical form is ISO 8601 UTC strings ending in ``Z``. Helpers here
accept epoch millis/seconds, ISO strings and datetimes, and always
normalise to UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


def parse_linkedin_datetime(value: Any) -> datetime | None:
    """Parse a LinkedIn timestamp into an aware UTC datetime.

    Accepts epoch milliseconds (int/float/str of digits), epoch seconds,
    ISO 8601 strings and datetimes. Returns None for empty / unparseable
    values instead of raising.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = str(value).strip()
    if not text:
        return None
    # Pure digits: epoch millis (>= 13 digits) or epoch seconds.
    if text.lstrip("-").isdigit():
        try:
            return _from_epoch(float(text))
        except (ValueError, OverflowError, OSError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _from_epoch(value: float) -> datetime | None:
    """Epoch seconds or millis (heuristic: > 1e11 is millis)."""
    try:
        seconds = value / 1000.0 if abs(value) >= 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def format_iso_utc(value: datetime | None) -> str | None:
    """Render an aware datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (or None)."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def generate_run_id(now: datetime | None = None) -> str:
    """Unique pipeline run id: ``YYYYMMDD_HHMMSS_<4 hex chars>``.

    Example: ``20261111_103000_a123``.
    """
    import secrets

    moment = now or now_utc()
    return f"{moment.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


def apply_lookback(watermark: datetime, lookback_minutes: int) -> datetime:
    """Move a watermark backwards by the lookback window.

    The lookback protects against posts published around the previous
    extraction boundary, which could otherwise be missed.
    """
    return watermark - timedelta(minutes=max(lookback_minutes, 0))


def normalise_series(series: pd.Series) -> pd.Series:
    """Parse a Series of LinkedIn timestamps to UTC datetimes.

    Handles epoch millis as well as ISO strings. Unparseable values
    become NaT (never an exception).
    """
    numeric = pd.to_numeric(series, errors="coerce")
    epoch_ms = pd.to_datetime(numeric, unit="ms", utc=True, errors="coerce")
    iso = pd.to_datetime(series, utc=True, errors="coerce")
    return epoch_ms.fillna(iso)
