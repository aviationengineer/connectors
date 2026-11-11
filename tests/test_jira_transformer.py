"""Tests for the Jira transformer (flattening, ADF, custom fields)."""

from __future__ import annotations

import pandas as pd
import pytest

from src.transformers.jira_transformer import SCHEMA_COLUMNS, JiraTransformer
from tests.conftest import BASE_URL, CUSTOM_FIELDS


@pytest.fixture
def transformer():
    return JiraTransformer(base_url=BASE_URL, custom_fields=CUSTOM_FIELDS)


def test_flatten_full_issue_has_all_columns(transformer, sample_issues):
    row = transformer.flatten_issue(sample_issues[0])
    for column in SCHEMA_COLUMNS:
        assert column in row
    assert row["issue_id"] == "10001"
    assert row["issue_key"] == "DATA-101"
    assert row["project_key"] == "DATA"
    assert row["project_name"] == "Data Platform"
    assert row["summary"] == "Build the ingestion pipeline"
    assert row["issue_type"] == "Story"
    assert row["status"] == "In Progress"
    assert row["status_category"] == "In Progress"
    assert row["priority"] == "High"
    assert row["reporter"] == "Priya Reporter"
    assert row["reporter_account_id"] == "acc-reporter-1"
    assert row["assignee"] == "Alex Assignee"
    assert row["parent_issue_key"] == "DATA-100"
    assert row["epic_key"] == "DATA-1"
    assert row["sprint"] == "Sprint 12"
    assert row["story_points"] == 5
    assert row["comment_count"] == 3
    assert row["attachment_count"] == 2
    # flatten_issue keeps lists raw; joining happens in transform_issues.
    assert row["labels"] == ["pipeline", "ingestion"]
    assert row["components"] == ["ETL"]
    assert row["fix_versions"] == ["1.0.0"]
    assert row["url"] == f"{BASE_URL}/browse/DATA-101"


def test_adf_description_text_extraction(transformer, sample_issues):
    row = transformer.flatten_issue(sample_issues[0])
    assert row["description"] == (
        "Ingest Jira issues nightly. Validate before writing."
    )
    row_3 = transformer.flatten_issue(sample_issues[2])
    assert row_3["description"] == "Only fetch what changed."


def test_minimal_issue_missing_fields_become_none(transformer, sample_issues):
    row = transformer.flatten_issue(sample_issues[1])
    assert row["issue_key"] == "DATA-102"
    assert row["assignee"] is None
    assert row["assignee_account_id"] is None
    assert row["priority"] is None
    assert row["priority_id"] is None
    assert row["story_points"] is None
    assert row["sprint"] is None
    assert row["description"] == "A plain-string description."
    assert row["comment_count"] == 0
    assert row["attachment_count"] == 0
    assert row["labels"] == []


def test_custom_field_mapping_business_value(transformer, sample_issues):
    row = transformer.flatten_issue(sample_issues[2])
    assert row["business_value"] == "High"
    assert row["product_owner"] == "Olivia Owner"
    assert row["issue_key"] == "DATA-103"


def test_unknown_custom_field_dropped_by_default(sample_issues):
    transformer = JiraTransformer(base_url=BASE_URL, custom_fields=CUSTOM_FIELDS)
    issue = sample_issues[0]
    issue["fields"]["customfield_99999"] = "mystery"
    row = transformer.flatten_issue(issue)
    assert "custom_customfield_99999" not in row


def test_unknown_custom_field_retained_when_enabled(sample_issues):
    transformer = JiraTransformer(
        base_url=BASE_URL,
        custom_fields=CUSTOM_FIELDS,
        retain_unknown_custom_fields=True,
    )
    issue = sample_issues[0]
    issue["fields"]["customfield_99999"] = "mystery"
    row = transformer.flatten_issue(issue)
    assert row["custom_customfield_99999"] == "mystery"


def test_dates_normalised_to_utc_datetimes(transformer, sample_issues):
    df = transformer.transform_issues(sample_issues)
    assert pd.api.types.is_datetime64_any_dtype(df["created"])
    assert pd.api.types.is_datetime64_any_dtype(df["updated"])
    created = df.loc[df["issue_key"] == "DATA-101", "created"].iloc[0]
    assert str(created) == "2026-11-01 09:00:00+00:00"
    updated = df.loc[df["issue_key"] == "DATA-103", "updated"].iloc[0]
    assert str(updated) == "2026-11-10 16:45:00+00:00"


def test_transform_issues_row_count_and_dtypes(sample_dataframe):
    assert len(sample_dataframe) == 3
    assert list(sample_dataframe["issue_key"]) == ["DATA-101", "DATA-102", "DATA-103"]
    assert pd.api.types.is_integer_dtype(sample_dataframe["comment_count"])
    assert pd.api.types.is_integer_dtype(sample_dataframe["attachment_count"])
    assert sample_dataframe["comment_count"].sum() == 4
    assert sample_dataframe["attachment_count"].sum() == 2
    assert sample_dataframe["story_points"].iloc[0] == 5
    # List columns are ";"-joined by transform_issues (normalize_lists).
    assert sample_dataframe["labels"].iloc[0] == "pipeline;ingestion"
    assert sample_dataframe["components"].iloc[0] == "ETL"
    assert sample_dataframe["labels"].iloc[1] == ""


def test_transform_issues_empty_list_returns_schema_columns(transformer):
    df = transformer.transform_issues([])
    assert len(df) == 0
    for column in SCHEMA_COLUMNS:
        assert column in df.columns
