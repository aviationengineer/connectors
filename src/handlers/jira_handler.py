"""High-level Jira extraction handler (spec §3).

:class:`JiraHandler` hides all Jira API complexity from the rest of the
application. It owns extraction only — it never writes CSV, Google
Sheets or metadata. Output is produced by the writers; orchestration
belongs to :class:`~src.pipeline.jira_pipeline.JiraPipeline`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.clients.jira_client import JiraClient
from src.transformers.jira_transformer import JiraTransformer
from src.utils.exceptions import JiraAPIError
from src.utils.logger import get_logger

logger = get_logger("jira_pipeline.jira_handler")


class JiraHandler:
    """Extract Jira data through the REST API.

    Args:
        base_url: Jira site URL, e.g. ``https://company.atlassian.net``.
        username: Email address for basic auth (Jira Cloud).
        api_token: API token (basic auth) or bearer token.
        auth_type: ``"basic"`` (default) or ``"bearer"``.
        timeout: Per-request timeout in seconds.
        page_size: Default page size for paginated search (max 100
            for Jira Cloud's ``/search/jql`` endpoint).
        client: Optional pre-built :class:`JiraClient` (dependency
            injection — used by tests to inject a mock).
        transformer: Optional :class:`JiraTransformer` (injected).
    """

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        api_token: str | None = None,
        auth_type: str = "basic",
        timeout: int = 30,
        page_size: int = 100,
        client: JiraClient | None = None,
        transformer: JiraTransformer | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.api_token = api_token
        self.page_size = min(max(page_size, 1), 100)
        self.client = client or JiraClient(
            base_url=self.base_url,
            username=username,
            api_token=api_token,
            auth_type=auth_type,
            timeout=timeout,
        )
        self.transformer = transformer or JiraTransformer(base_url=self.base_url)

    # -- connection ---------------------------------------------------------
    def test_connection(self) -> bool:
        """Verify credentials by calling ``/myself``.

        Returns True on success; raises :class:`JiraAuthError` (or a
        connection error) on failure — never returns a silent False.
        """
        logger.info("Testing Jira connection to %s", self.base_url)
        myself = self.client.get_myself()
        logger.info(
            "Jira connection successful (authenticated as %s)",
            myself.get("displayName") or myself.get("emailAddress") or "unknown",
        )
        return True

    # -- projects -----------------------------------------------------------
    def get_projects(self) -> list[dict[str, Any]]:
        """Return all visible Jira projects."""
        projects = self.client.get_projects()
        logger.info("Retrieved %d Jira projects", len(projects))
        return projects

    def get_project(self, project_key: str) -> dict[str, Any]:
        """Return one project's details."""
        return self.client.get_project(project_key)

    # -- issue search -------------------------------------------------------
    def search_issues(
        self,
        jql: str,
        fields: list[str] | None = None,
        start_at: int = 0,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Run one paged JQL search and return the raw response."""
        logger.debug(
            "Jira search: startAt=%s maxResults=%s fields=%s",
            start_at,
            max_results,
            fields,
        )
        return self.client.search_jql(
            jql, fields=fields, start_at=start_at, max_results=max_results
        )

    def get_all_issues(
        self,
        jql: str,
        fields: list[str] | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve *all* issues for a JQL query, paging to the end.

        Logs progress per page (§9) and stops when the reported total
        is reached or a short page is returned.
        """
        size = min(page_size or self.page_size, 100)
        logger.info("Retrieving Jira issues...")
        logger.info("JQL: %s", jql)
        all_issues: list[dict[str, Any]] = []
        start_at = 0
        page = 1
        total: int | None = None
        while True:
            response = self.search_issues(
                jql, fields=fields, start_at=start_at, max_results=size
            )
            issues = response.get("issues", [])
            if total is None:
                total = int(response.get("total", len(issues)))
            all_issues.extend(issues)
            logger.info(
                "Page: %d | Records retrieved: %d | Total retrieved: %d",
                page,
                len(issues),
                len(all_issues),
            )
            if total is not None and len(all_issues) >= total:
                break
            if len(issues) < size:
                # Short page with no total to chase: done.
                break
            start_at += len(issues)
            page += 1
        logger.info("Total Jira issues retrieved: %d", len(all_issues))
        return all_issues

    # -- single issue -------------------------------------------------------
    def get_issue(
        self, issue_key: str, fields: list[str] | None = None
    ) -> dict[str, Any]:
        """Return one issue's full payload."""
        return self.client.get_issue(issue_key, fields=fields)

    def get_issue_comments(self, issue_key: str) -> list[dict[str, Any]]:
        """Return the comments on one issue."""
        data = self.client.get_issue_comments(issue_key)
        return data.get("comments", [])

    def get_issue_worklogs(self, issue_key: str) -> list[dict[str, Any]]:
        """Return the worklogs on one issue."""
        data = self.client.get_issue_worklogs(issue_key)
        return data.get("worklogs", [])

    def get_issue_changelog(self, issue_key: str) -> list[dict[str, Any]]:
        """Return the changelog (history) of one issue."""
        data = self.client.get_issue_changelog(issue_key)
        return data.get("values", [])

    # -- agile / users ------------------------------------------------------
    def get_sprints(self, board_id: int | str) -> list[dict[str, Any]]:
        """Return the sprints on an agile board."""
        data = self.client.get_sprints(board_id)
        return data.get("values", [])

    def get_users(self, query: str = "") -> list[dict[str, Any]]:
        """Return Jira users (optionally filtered by a query string)."""
        return self.client.get_users(query=query)

    # -- extraction conveniences --------------------------------------------
    def extract_issues(
        self,
        jql: str,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract all issues for a JQL query as raw JSON dicts."""
        return self.get_all_issues(jql, fields=fields)

    def extract_to_dataframe(
        self,
        jql: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """Extract all issues for a JQL query as a standardized DataFrame."""
        issues = self.get_all_issues(jql, fields=fields)
        if not issues:
            logger.info("JQL returned no issues; returning an empty DataFrame")
            return pd.DataFrame(columns=self.transformer._output_columns())
        df = self.transformer.transform_issues(issues)
        logger.info("Transformation completed: %d records", len(df))
        return df
