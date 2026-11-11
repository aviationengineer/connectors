"""Date and time helpers.

Jira returns ISO 8601 timestamps with a zone offset
(e.g. ``2026-11-09T14:32:00.000+0000``). All timestamps are normalised
to UTC and rendered as ISO 8601 strings ending in ``Z``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


def parse_jira_datetime(value: Any) -> datetime | None:
    """Parse a Jira timestamp string into an aware UTC datetime.

    Returns None for empty / unparseable values instead of raising.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    # Jira uses +0000 (no colon) offsets; fromisoformat needs +00:00.
    if len(text) >= 5 and (text[-5] in "+-") and text[-2:].isdigit():
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_iso_utc(value: datetime | None) -> str | None:
    """Render an aware datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (or None)."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_jql_datetime(value: datetime) -> str:
    """Render a datetime in JQL's ``"yyyy-MM-dd HH:mm"`` literal format."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def now_utc() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def generate_run_id(now: datetime | None = None) -> str:
    """Unique pipeline run id: ``YYYYMMDD_HHMMSS_<4 hex chars>``.

    Example: ``20261110_193001_a82f``.
    """
    import secrets

    moment = now or now_utc()
    return f"{moment.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


def apply_lookback(watermark: datetime, lookback_minutes: int) -> datetime:
    """Move a watermark backwards by the lookback window.

    The lookback protects against records updated around the previous
    extraction boundary, which could otherwise be missed.
    """
    return watermark - timedelta(minutes=max(lookback_minutes, 0))


def normalise_series(series: pd.Series) -> pd.Series:
    """Parse a Series of Jira timestamp strings to UTC datetimes.

    Unparseable values become NaT (never an exception).
    """
    return pd.to_datetime(series, utc=True, errors="coerce")
