"""Tests for the LinkedIn REST API client — HTTP layer fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.clients.linkedin_client import LinkedInClient
from src.utils.exceptions import (
    LinkedInAuthError,
    LinkedInForbiddenError,
    LinkedInNotFoundError,
    LinkedInRateLimitError,
    LinkedInServerError,
)


def _response(status: int, payload: dict | None = None, headers: dict | None = None):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.content = b"{}" if payload is None else b"x"
    response.text = "" if payload is None else str(payload)
    response.json.return_value = payload or {}
    return response


@pytest.fixture()
def client() -> LinkedInClient:
    return LinkedInClient(access_token="test-token", max_retries=2)


def test_auth_and_version_headers(client: LinkedInClient) -> None:
    with patch.object(
        client.session, "request", return_value=_response(200, {"id": "x"})
    ) as request:
        client.get_me()
    _, kwargs = request.call_args
    assert request.call_args[0][1] == "https://api.linkedin.com/rest/me"
    assert client.session.headers["Authorization"] == "Bearer test-token"
    assert client.session.headers["LinkedIn-Version"] == "202610"
    assert client.session.headers["X-Restli-Protocol-Version"] == "2.0.0"


def test_get_me_and_organization_endpoints(client: LinkedInClient) -> None:
    with patch.object(
        client.session, "request", return_value=_response(200, {"id": "1"})
    ) as request:
        client.get_me()
        assert request.call_args[0][1].endswith("/rest/me")
    with patch.object(
        client.session, "request", return_value=_response(200, {"id": 123})
    ) as request:
        client.get_organization("urn:li:organization:123")
        assert request.call_args[0][1].endswith("/rest/organizations/123")


def test_list_posts_params(client: LinkedInClient) -> None:
    with patch.object(
        client.session, "request", return_value=_response(200, {"elements": []})
    ) as request:
        client.list_posts("urn:li:organization:123", count=5, start=10)
    _, kwargs = request.call_args
    params = kwargs["params"]
    assert params["q"] == "author"
    assert params["author"] == "urn:li:organization:123"
    assert params["count"] == 5
    assert params["start"] == 10


def test_organization_acls_returns_elements(client: LinkedInClient) -> None:
    payload = {"elements": [{"organization": "urn:li:organization:1"}]}
    with patch.object(client.session, "request", return_value=_response(200, payload)):
        assert client.list_organization_acls() == payload["elements"]


@pytest.mark.parametrize(
    "status,error",
    [
        (401, LinkedInAuthError),
        (403, LinkedInForbiddenError),
        (404, LinkedInNotFoundError),
    ],
)
def test_permanent_errors_not_retried(
    client: LinkedInClient, status: int, error: type[Exception]
) -> None:
    with patch.object(
        client.session, "request", return_value=_response(status)
    ) as request:
        with pytest.raises(error):
            client.get_me()
    assert request.call_count == 1  # never retried


def test_rate_limit_retried_then_succeeds(client: LinkedInClient) -> None:
    calls = [_response(429), _response(200, {"id": "x"})]
    with patch.object(client.session, "request", side_effect=calls) as request:
        assert client.get_me() == {"id": "x"}
    assert request.call_count == 2
    assert client.retry_count >= 1


def test_server_error_retried_then_raises(client: LinkedInClient) -> None:
    with patch.object(
        client.session, "request", return_value=_response(500)
    ) as request:
        with pytest.raises(LinkedInServerError):
            client.get_me()
    assert request.call_count == 3  # initial + 2 retries


def test_connection_error_retried(client: LinkedInClient) -> None:
    with patch.object(
        client.session,
        "request",
        side_effect=requests.exceptions.ConnectionError("down"),
    ) as request:
        with pytest.raises(Exception):
            client.get_me()
    assert request.call_count == 3


def test_set_access_token_updates_header(client: LinkedInClient) -> None:
    client.set_access_token("new-token")
    assert client.session.headers["Authorization"] == "Bearer new-token"


def test_exchange_code_for_tokens() -> None:
    payload = {"access_token": "abc", "expires_in": 5184000}
    with patch(
        "src.clients.linkedin_client.requests.post",
        return_value=_response(200, payload),
    ) as post:
        result = LinkedInClient.exchange_code_for_tokens(
            "cid", "csecret", "code123", "http://localhost:8080"
        )
    assert result == payload
    _, kwargs = post.call_args
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "code123"


def test_exchange_code_failure_raises() -> None:
    with patch(
        "src.clients.linkedin_client.requests.post",
        return_value=_response(400, {"error": "invalid_grant"}),
    ):
        with pytest.raises(LinkedInAuthError):
            LinkedInClient.exchange_code_for_tokens(
                "cid", "csecret", "bad", "http://localhost:8080"
            )


def test_refresh_access_token() -> None:
    payload = {"access_token": "refreshed", "expires_in": 5184000}
    with patch(
        "src.clients.linkedin_client.requests.post",
        return_value=_response(200, payload),
    ) as post:
        result = LinkedInClient.refresh_access_token("cid", "csecret", "refresh-me")
    assert result["access_token"] == "refreshed"
    assert post.call_args[1]["data"]["grant_type"] == "refresh_token"
