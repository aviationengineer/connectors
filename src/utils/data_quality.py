"""Data quality checks for the standardized Jira DataFrame (spec §19, §45).

Validation runs before any output is written. Rows missing the primary
key (``issue_id`` / ``issue_key``) are invalid; unparseable dates are
flagged; duplicates are counted. Warnings never fail the pipeline on
their own — only a fully invalid frame does.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

REQUIRED_COLUMNS = ["issue_id", "issue_key", "summary", "created", "updated"]


def validate_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Validate a transformed Jira DataFrame and return a quality summary.

    The returned mapping matches the §45 data-quality shape::

        {
          "status": "PASS" | "WARN" | "FAIL",
          "total_records": ...,
          "valid_records": ...,
          "invalid_records": ...,
          "missing_issue_id": ...,
          "missing_issue_key": ...,
          "missing_summary": ...,
          "duplicate_issue_id": ...,
          "duplicate_issue_key": ...,
          "invalid_created_dates": ...,
          "invalid_updated_dates": ...,
          "missing_assignee": ...,
          "missing_reporter": ...,
          "missing_status": ...,
        }
    """
    total = len(df)
    summary: dict[str, Any] = {
        "status": "PASS",
        "total_records": total,
        "valid_records": total,
        "invalid_records": 0,
        "missing_issue_id": 0,
        "missing_issue_key": 0,
        "missing_summary": 0,
        "duplicate_issue_id": 0,
        "duplicate_issue_key": 0,
        "invalid_created_dates": 0,
        "invalid_updated_dates": 0,
        "missing_assignee": 0,
        "missing_reporter": 0,
        "missing_status": 0,
    }
    if total == 0:
        return summary

    def _missing(column: str) -> int:
        if column not in df.columns:
            return total
        return int(df[column].isna().sum() + (df[column].astype(str) == "").sum())

    summary["missing_issue_id"] = _missing("issue_id")
    summary["missing_issue_key"] = _missing("issue_key")
    summary["missing_summary"] = _missing("summary")
    summary["missing_assignee"] = _missing("assignee")
    summary["missing_reporter"] = _missing("reporter")
    summary["missing_status"] = _missing("status")

    if "issue_id" in df.columns:
        summary["duplicate_issue_id"] = int(df["issue_id"].duplicated().sum())
    if "issue_key" in df.columns:
        summary["duplicate_issue_key"] = int(df["issue_key"].duplicated().sum())

    for column, key in (("created", "invalid_created_dates"),
                        ("updated", "invalid_updated_dates")):
        if column in df.columns:
            parsed = pd.to_datetime(df[column], utc=True, errors="coerce")
            present = df[column].notna() & (df[column].astype(str) != "")
            summary[key] = int((parsed.isna() & present).sum())

    # A row is invalid when it has no usable primary key or a bad date.
    invalid = 0
    if "issue_id" in df.columns and "issue_key" in df.columns:
        no_key = df["issue_id"].isna() & df["issue_key"].isna()
        invalid = int(no_key.sum())
    summary["invalid_records"] = invalid
    summary["valid_records"] = total - invalid

    if total == 0:
        summary["status"] = "PASS"
    elif invalid == total:
        summary["status"] = "FAIL"
    elif invalid > 0 or summary["duplicate_issue_id"] > 0:
        summary["status"] = "WARN"
    return summary


def check_required_columns(df: pd.DataFrame, required: list[str] | None = None) -> list[str]:
    """Return the list of required columns missing from the DataFrame."""
    required = required if required is not None else REQUIRED_COLUMNS
    return [column for column in required if column not in df.columns]


def drop_invalid_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with neither ``issue_id`` nor ``issue_key`` set."""
    if "issue_id" not in df.columns or "issue_key" not in df.columns:
        return df
    mask = df["issue_id"].notna() | df["issue_key"].notna()
    return df.loc[mask].reset_index(drop=True)
