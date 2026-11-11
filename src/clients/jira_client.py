"""Low-level Jira REST API client.

A thin wrapper around :mod:`requests` that owns authentication,
retries and error mapping — nothing else. It knows nothing about
DataFrames, CSV or Google Sheets.

Retry policy (spec §18)
-----------------------
* Retried with exponential backoff: 429 (rate limit), 500, 502, 503,
  network timeouts and connection failures.
* Never retried: 401 / 403 (bad credentials) and 400 invalid JQL.
"""

from __future__ import annotations

from typing import Any

import requests
import tenacity

from src.utils.exceptions import (
    InvalidJQLError,
    JiraAPIError,
    JiraAuthError,
    JiraConnectionError,
    JiraRateLimitError,
    JiraServerError,
)
from src.utils.logger import get_logger

logger = get_logger("jira_pipeline.jira_client")

# Statuses worth retrying. 401/403/400 are permanent and never retried.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (JiraRateLimitError, JiraServerError, JiraConnectionError))


class JiraClient:
    """HTTP client for the Jira Cloud REST API (v3 by default).

    Args:
        base_url: Jira site URL, e.g. ``https://company.atlassian.net``.
        username: Email address used for basic auth (Jira Cloud).
        api_token: API token for basic auth, or a bearer token when
            ``auth_type="bearer"``.
        auth_type: ``"basic"`` (default) or ``"bearer"``.
        timeout: Per-request timeout in seconds.
        max_retries: How many times a retryable failure is retried.
        api_version: REST API version segment (``"3"`` for Cloud).
    """

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        api_token: str | None = None,
        auth_type: str = "basic",
        timeout: int = 30,
        max_retries: int = 3,
        api_version: str = "3",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_token = api_token
        self.auth_type = auth_type.lower()
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_version = api_version
        self.retry_count = 0
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )
        if self.auth_type == "basic" and username and api_token:
            self.session.auth = (username, api_token)
        elif self.auth_type == "bearer" and api_token:
            self.session.headers["Authorization"] = f"Bearer {api_token}"

    # -- internals ----------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _map_status(self, status: int, body: str) -> JiraAPIError:
        if status in (401, 403):
            return JiraAuthError(
                f"Jira authentication failed (HTTP {status}). Check the "
                "email and API token.",
                status_code=status,
                response_body=body,
            )
        if status == 400:
            return InvalidJQLError(
                f"Invalid JQL or bad request (HTTP 400): {body[:500]}",
                status_code=status,
                response_body=body,
            )
        if status == 404:
            return JiraAPIError(
                f"Jira resource not found (HTTP 404): {body[:500]}",
                status_code=status,
                response_body=body,
            )
        if status == 429:
            return JiraRateLimitError(
                "Jira rate limit reached (HTTP 429).",
                status_code=status,
                response_body=body,
            )
        if status >= 500:
            return JiraServerError(
                f"Jira server error (HTTP {status}).",
                status_code=status,
                response_body=body,
            )
        return JiraAPIError(
            f"Jira API error (HTTP {status}): {body[:500]}",
            status_code=status,
            response_body=body,
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request with exponential backoff on retryable failures."""
        url = self._url(path)

        def _attempt() -> dict[str, Any]:
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    timeout=self.timeout,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                raise JiraConnectionError(
                    f"Could not reach Jira at {self.base_url}: {exc}"
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise JiraConnectionError(f"Jira request failed: {exc}") from exc

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning(
                    "Jira rate limit (429)%s",
                    f"; Retry-After: {retry_after}s" if retry_after else "",
                )
            if response.status_code >= 400:
                raise self._map_status(response.status_code, response.text)
            try:
                data = response.json()
            except ValueError as exc:
                raise JiraAPIError(
                    f"Malformed JSON in Jira response from {path}",
                    status_code=response.status_code,
                ) from exc
            return data

        retrying = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(self.max_retries + 1),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=30),
            retry=tenacity.retry_if_exception(_is_retryable),
            reraise=True,
            before_sleep=lambda state: logger.warning(
                "Retrying Jira request (attempt %s/%s): %s",
                state.attempt_number,
                self.max_retries + 1,
                state.outcome.exception(),
            ),
        )
        try:
            result = retrying(_attempt)
        except (JiraRateLimitError, JiraServerError, JiraConnectionError):
            self.retry_count += 1
            raise
        self.retry_count += max(retrying.statistics.get("attempt_number", 1) - 1, 0)
        return result

    # -- generic verbs ------------------------------------------------------
    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("Jira GET %s params=%s", path, params)
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("Jira POST %s", path)
        return self._request("POST", path, payload=payload)

    # -- Jira endpoints -----------------------------------------------------
    def search_jql(
        self,
        jql: str,
        fields: list[str] | None = None,
        start_at: int = 0,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """POST /rest/api/{v}/search/jql — the current Cloud search endpoint."""
        payload: dict[str, Any] = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": max_results,
        }
        if fields:
            payload["fields"] = fields
        return self.post(f"/rest/api/{self.api_version}/search/jql", payload=payload)

    def get_myself(self) -> dict[str, Any]:
        return self.get(f"/rest/api/{self.api_version}/myself")

    def get_projects(self) -> list[dict[str, Any]]:
        data = self.get(f"/rest/api/{self.api_version}/project")
        return data.get("values", data) if isinstance(data, dict) else data  # type: ignore[return-value]

    def get_project(self, project_key: str) -> dict[str, Any]:
        return self.get(f"/rest/api/{self.api_version}/project/{project_key}")

    def get_issue(self, issue_key: str, fields: list[str] | None = None) -> dict[str, Any]:
        params = {"fields": ",".join(fields)} if fields else None
        return self.get(f"/rest/api/{self.api_version}/issue/{issue_key}", params=params)

    def get_issue_comments(self, issue_key: str) -> dict[str, Any]:
        return self.get(f"/rest/api/{self.api_version}/issue/{issue_key}/comment")

    def get_issue_worklogs(self, issue_key: str) -> dict[str, Any]:
        return self.get(f"/rest/api/{self.api_version}/issue/{issue_key}/worklog")

    def get_issue_changelog(self, issue_key: str) -> dict[str, Any]:
        return self.get(f"/rest/api/{self.api_version}/issue/{issue_key}/changelog")

    def get_sprints(self, board_id: int | str) -> dict[str, Any]:
        return self.get(f"/rest/agile/1.0/board/{board_id}/sprint")

    def get_users(self, query: str = "") -> list[dict[str, Any]]:
        data = self.get(
            f"/rest/api/{self.api_version}/users/search",
            params={"query": query} if query else None,
        )
        return data.get("values", []) if isinstance(data, dict) else data  # type: ignore[return-value]
