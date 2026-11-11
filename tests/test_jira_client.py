"""Tests for the low-level Jira REST client (retries, error mapping)."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests
import tenacity

from src.clients.jira_client import JiraClient
from src.utils.exceptions import (
    InvalidJQLError,
    JiraAPIError,
    JiraAuthError,
)

BASE_URL = "https://example.atlassian.net"


@pytest.fixture
def fast_retry(monkeypatch):
    """Make tenacity retries instant so retry tests stay fast."""
    monkeypatch.setattr(
        tenacity,
        "wait_exponential",
        lambda *args, **kwargs: tenacity.wait_none(),
    )


@pytest.fixture
def client(fast_retry):
    """A client with a mocked session (no network)."""
    client = JiraClient(
        base_url=BASE_URL,
        username="user@example.com",
        api_token="test-token",
        max_retries=2,
    )
    client.session.request = Mock()
    return client


def _response(status: int, payload, headers: dict | None = None) -> Mock:
    response = Mock()
    response.status_code = status
    response.headers = headers or {}
    if isinstance(payload, Exception):
        response.text = ""
        response.json = Mock(side_effect=payload)
    else:
        response.text = payload if isinstance(payload, str) else json.dumps(payload)
        response.json = Mock(return_value=payload)
    return response


def test_get_200_returns_parsed_json(client):
    client.session.request.return_value = _response(
        200, {"displayName": "Priya Reporter"}
    )
    assert client.get_myself() == {"displayName": "Priya Reporter"}
    client.session.request.assert_called_once()


def test_401_raises_auth_error_without_retry(client):
    client.session.request.return_value = _response(401, "Unauthorized")
    with pytest.raises(JiraAuthError):
        client.get_myself()
    assert client.session.request.call_count == 1


def test_403_raises_auth_error_without_retry(client):
    client.session.request.return_value = _response(403, "Forbidden")
    with pytest.raises(JiraAuthError):
        client.get_myself()
    assert client.session.request.call_count == 1


def test_400_raises_invalid_jql_without_retry(client):
    client.session.request.return_value = _response(400, "Error in JQL")
    with pytest.raises(InvalidJQLError):
        client.search_jql("project = BAD")
    assert client.session.request.call_count == 1


def test_429_then_200_succeeds_after_retry(client):
    client.session.request.side_effect = [
        _response(429, "Too Many Requests", headers={"Retry-After": "1"}),
        _response(200, {"total": 0, "issues": []}),
    ]
    result = client.search_jql("project = DATA")
    assert result == {"total": 0, "issues": []}
    assert client.session.request.call_count == 2


def test_500_then_200_succeeds_after_retry(client):
    client.session.request.side_effect = [
        _response(500, "Server error"),
        _response(200, {"key": "DATA"}),
    ]
    assert client.get_project("DATA") == {"key": "DATA"}
    assert client.session.request.call_count == 2


def test_timeout_then_200_succeeds_after_retry(client):
    client.session.request.side_effect = [
        requests.exceptions.Timeout("timed out"),
        _response(200, {"displayName": "Priya Reporter"}),
    ]
    assert client.get_myself() == {"displayName": "Priya Reporter"}
    assert client.session.request.call_count == 2


def test_search_jql_posts_to_search_endpoint_with_payload(client):
    client.session.request.return_value = _response(200, {"total": 0, "issues": []})
    client.search_jql(
        "project = DATA ORDER BY updated DESC",
        fields=["summary", "status"],
        max_results=50,
        next_page_token="tok-abc",
    )
    args, kwargs = client.session.request.call_args
    assert args[0] == "POST"
    assert args[1] == f"{BASE_URL}/rest/api/3/search/jql"
    assert kwargs["json"] == {
        "jql": "project = DATA ORDER BY updated DESC",
        "maxResults": 50,
        "fields": ["summary", "status"],
        "nextPageToken": "tok-abc",
    }


def test_search_jql_omits_next_page_token_when_none(client):
    client.session.request.return_value = _response(200, {"total": 0, "issues": []})
    client.search_jql("project = DATA")
    _, kwargs = client.session.request.call_args
    assert "nextPageToken" not in kwargs["json"]
    assert "startAt" not in kwargs["json"]


def test_search_jql_defaults_to_all_fields(client):
    # The /search/jql endpoint returns only the issue ID when no fields
    # are requested, so an omitted fields list must become ["*all"].
    client.session.request.return_value = _response(200, {"total": 0, "issues": []})
    client.search_jql("project = DATA")
    _, kwargs = client.session.request.call_args
    assert kwargs["json"]["fields"] == ["*all"]


def test_get_projects_handles_list_response(client):
    projects = [{"key": "DATA", "name": "Data Platform"}, {"key": "TECH"}]
    client.session.request.return_value = _response(200, projects)
    assert client.get_projects() == projects


def test_malformed_json_raises_api_error(client):
    client.session.request.return_value = _response(200, ValueError("bad json"))
    with pytest.raises(JiraAPIError, match="Malformed JSON"):
        client.get_myself()
