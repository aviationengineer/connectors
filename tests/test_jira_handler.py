"""Tests for the Jira extraction handler (pagination, delegation)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.handlers.jira_handler import JiraHandler
from src.transformers.jira_transformer import SCHEMA_COLUMNS, JiraTransformer
from tests.conftest import BASE_URL, CUSTOM_FIELDS


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def handler(mock_client):
    return JiraHandler(
        base_url=BASE_URL,
        username="user@example.com",
        api_token="test-token",
        page_size=100,
        client=mock_client,
        transformer=JiraTransformer(base_url=BASE_URL, custom_fields=CUSTOM_FIELDS),
    )


def _page(issues, total):
    return {"total": total, "issues": issues}


def _token_page(issues, token=None, is_last=True):
    page: dict = {"issues": issues, "isLast": is_last}
    if token is not None:
        page["nextPageToken"] = token
    return page


def test_get_all_issues_paginates_across_three_pages(handler, mock_client, sample_issues):
    pages = [
        _token_page(
            sample_issues[0:1] + [dict(sample_issues[1], key="DATA-104")],
            token="tok-1",
            is_last=False,
        ),
        _token_page(
            [dict(sample_issues[2], key="DATA-105"), dict(sample_issues[0], key="DATA-106")],
            token="tok-2",
            is_last=False,
        ),
        _token_page([dict(sample_issues[1], key="DATA-107")], is_last=True),
    ]
    mock_client.search_jql.side_effect = pages
    issues = handler.get_all_issues("project = DATA", page_size=2)
    assert len(issues) == 5
    assert mock_client.search_jql.call_count == 3
    tokens = [call.kwargs["next_page_token"] for call in mock_client.search_jql.call_args_list]
    assert tokens == [None, "tok-1", "tok-2"]


def test_get_all_issues_stops_at_total(handler, mock_client, sample_issues):
    mock_client.search_jql.return_value = _page(sample_issues, 3)
    issues = handler.get_all_issues("project = DATA")
    assert len(issues) == 3
    mock_client.search_jql.assert_called_once()


def test_extract_to_dataframe_has_schema_columns(handler, mock_client, sample_issues):
    mock_client.search_jql.return_value = _page(sample_issues, 3)
    df = handler.extract_to_dataframe("project = DATA")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    for column in SCHEMA_COLUMNS:
        assert column in df.columns
    assert list(df["issue_key"]) == ["DATA-101", "DATA-102", "DATA-103"]


def test_extract_to_dataframe_empty_result_keeps_schema(handler, mock_client):
    mock_client.search_jql.return_value = _page([], 0)
    df = handler.extract_to_dataframe("project = EMPTY")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    for column in SCHEMA_COLUMNS:
        assert column in df.columns


def test_test_connection_returns_true_and_logs(handler, mock_client):
    mock_client.get_myself.return_value = {"displayName": "Priya Reporter"}
    assert handler.test_connection() is True
    mock_client.get_myself.assert_called_once()


def test_get_issue_comments_unwraps_comments(handler, mock_client):
    mock_client.get_issue_comments.return_value = {
        "comments": [{"id": "1", "body": "hello"}, {"id": "2", "body": "world"}]
    }
    comments = handler.get_issue_comments("DATA-101")
    assert len(comments) == 2
    assert comments[0]["body"] == "hello"
    mock_client.get_issue_comments.assert_called_once_with("DATA-101")


def test_get_issue_worklogs_unwraps_worklogs(handler, mock_client):
    mock_client.get_issue_worklogs.return_value = {
        "worklogs": [{"id": "10", "timeSpentSeconds": 3600}]
    }
    worklogs = handler.get_issue_worklogs("DATA-101")
    assert worklogs == [{"id": "10", "timeSpentSeconds": 3600}]


def test_get_issue_changelog_unwraps_values(handler, mock_client):
    mock_client.get_issue_changelog.return_value = {
        "values": [{"id": "100", "items": []}]
    }
    changelog = handler.get_issue_changelog("DATA-101")
    assert changelog == [{"id": "100", "items": []}]


def test_get_sprints_unwraps_values(handler, mock_client):
    mock_client.get_sprints.return_value = {
        "values": [{"id": 5, "name": "Sprint 12"}]
    }
    sprints = handler.get_sprints(7)
    assert sprints == [{"id": 5, "name": "Sprint 12"}]
    mock_client.get_sprints.assert_called_once_with(7)


def test_get_users_delegates_query(handler, mock_client):
    mock_client.get_users.return_value = [{"accountId": "acc-1"}]
    assert handler.get_users("priya") == [{"accountId": "acc-1"}]
    mock_client.get_users.assert_called_once_with(query="priya")


def test_search_issues_passes_fields_through(handler, mock_client):
    mock_client.search_jql.return_value = {"total": 0, "issues": []}
    handler.search_issues(
        "project = DATA", fields=["summary", "status"], max_results=25, next_page_token="tok-9"
    )
    mock_client.search_jql.assert_called_once_with(
        "project = DATA",
        fields=["summary", "status"],
        max_results=25,
        next_page_token="tok-9",
    )
