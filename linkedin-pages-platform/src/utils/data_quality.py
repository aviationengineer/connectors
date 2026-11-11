"""Data quality checks for the standardised LinkedIn DataFrame.

Validation runs before any output is written. Rows missing the primary
key (``post_urn``) are invalid; unparseable dates and malformed URLs are
flagged; duplicates are counted. Warnings never fail the pipeline on
their own — only a fully invalid frame does.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from src.utils.date_utils import parse_linkedin_datetime

_URL_PATTERN = re.compile(r"^https?://\S+$")


def validate_dataframe(
    df: pd.DataFrame,
    required_columns: list[str] | None = None,
    business_key: str = "post_urn",
    date_columns: list[str] | None = None,
    url_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Validate a transformed LinkedIn DataFrame; return a quality summary.

    Returned mapping::

        {
          "status": "PASS" | "WARN" | "FAIL",
          "total_records": ...,
          "valid_records": ...,
          "invalid_records": ...,
          "missing_business_key": ...,
          "duplicate_business_key": ...,
          "invalid_dates": ...,
          "invalid_urls": ...,
          "missing_required": {column: count, ...},
          "null_percentage": {column: pct, ...},
        }
    """
    required_columns = required_columns or ["post_urn", "organization_urn", "created_at"]
    date_columns = date_columns or ["created_at"]
    url_columns = url_columns or ["post_url"]
    total = len(df)
    summary: dict[str, Any] = {
        "status": "PASS",
        "total_records": total,
        "valid_records": total,
        "invalid_records": 0,
        "missing_business_key": 0,
        "duplicate_business_key": 0,
        "invalid_dates": 0,
        "invalid_urls": 0,
        "missing_required": {},
        "null_percentage": {},
    }
    if total == 0:
        return summary

    def _missing(column: str) -> int:
        if column not in df.columns:
            return total
        series = df[column]
        return int(series.isna().sum() + (series.astype(str) == "").sum())

    for column in required_columns:
        summary["missing_required"][column] = _missing(column)

    summary["missing_business_key"] = _missing(business_key)
    if business_key in df.columns:
        summary["duplicate_business_key"] = int(
            df[business_key].dropna().duplicated().sum()
        )

    for column in date_columns:
        if column not in df.columns:
            continue
        present = df[column].notna() & (df[column].astype(str) != "")
        unparsed = sum(
            1
            for value in df.loc[present, column]
            if parse_linkedin_datetime(value) is None
        )
        summary["invalid_dates"] += unparsed

    for column in url_columns:
        if column not in df.columns:
            continue
        present = df[column].notna() & (df[column].astype(str) != "")
        bad = sum(
            1
            for value in df.loc[present, column]
            if not _URL_PATTERN.match(str(value).strip())
        )
        summary["invalid_urls"] += bad

    summary["null_percentage"] = {
        str(column): round(float(df[column].isna().mean() * 100), 2)
        for column in df.columns
    }

    # A row is invalid when it has no usable business key.
    if business_key in df.columns:
        no_key = df[business_key].isna() | (
            df[business_key].astype(str) == ""
        )
        summary["invalid_records"] = int(no_key.sum())
    summary["valid_records"] = total - summary["invalid_records"]

    if summary["invalid_records"] == total:
        summary["status"] = "FAIL"
    elif (
        summary["invalid_records"] > 0
        or summary["duplicate_business_key"] > 0
        or summary["invalid_dates"] > 0
        or summary["invalid_urls"] > 0
    ):
        summary["status"] = "WARN"
    return summary


def check_required_columns(
    df: pd.DataFrame, required: list[str] | None = None
) -> list[str]:
    """Return the list of required columns missing from the DataFrame."""
    required = required or ["post_urn", "organization_urn", "created_at"]
    return [column for column in required if column not in df.columns]


def drop_invalid_rows(df: pd.DataFrame, business_key: str = "post_urn") -> pd.DataFrame:
    """Drop rows with no usable business key."""
    if business_key not in df.columns:
        return df
    mask = df[business_key].notna() & (df[business_key].astype(str) != "")
    return df.loc[mask].reset_index(drop=True)
