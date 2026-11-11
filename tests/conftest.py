"""Shared fixtures for the Jira framework test suite.

The fixtures build realistic Jira payloads: one full issue (ADF
description, custom fields, sprint, parent/epic, comments and
attachments), one minimal issue (missing assignee/priority/custom
fields), and one later-updated issue carrying a business-value custom
field. No test in this suite touches the network or real credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.transformers.jira_transformer import JiraTransformer  # noqa: E402

BASE_URL = "https://example.atlassian.net"

CUSTOM_FIELDS = {
    "customfield_10001": "story_points",
    "customfield_10002": "business_value",
    "customfield_10003": "product_owner",
    "customfield_10020": "sprint",
    "customfield_10014": "epic_link",
}


def _user(account_id: str, name: str) -> dict:
    return {
        "accountId": account_id,
        "displayName": name,
        "emailAddress": f"{name.lower().replace(' ', '.')}@example.com",
        "active": True,
    }


@pytest.fixture
def sample_issues() -> list[dict]:
    """Three raw Jira issue dicts: full, minimal, later-updated."""
    issue_1 = {
        "id": "10001",
        "key": "DATA-101",
        "self": f"{BASE_URL}/rest/api/3/issue/10001",
        "fields": {
            "summary": "Build the ingestion pipeline",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "Ingest Jira issues nightly."}
                        ],
                    },
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "Validate before writing."}
                        ],
                    },
                ],
            },
            "issuetype": {"id": "10001", "name": "Story"},
            "project": {"id": "10000", "key": "DATA", "name": "Data Platform"},
            "status": {
                "id": "10002",
                "name": "In Progress",
                "statusCategory": {"id": 4, "name": "In Progress"},
            },
            "priority": {"id": "2", "name": "High"},
            "resolution": None,
            "resolutiondate": None,
            "reporter": _user("acc-reporter-1", "Priya Reporter"),
            "assignee": _user("acc-assignee-1", "Alex Assignee"),
            "creator": _user("acc-reporter-1", "Priya Reporter"),
            "created": "2026-11-01T09:00:00.000+0000",
            "updated": "2026-11-09T14:32:00.000+0000",
            "duedate": "2026-11-20",
            "labels": ["pipeline", "ingestion"],
            "components": [{"id": "10010", "name": "ETL"}],
            "fixVersions": [{"id": "10020", "name": "1.0.0"}],
            "versions": [{"id": "10021", "name": "0.9.0"}],
            "parent": {"id": "10000", "key": "DATA-100"},
            "epic": {"id": "10005", "key": "DATA-1"},
            "comment": {"total": 3, "comments": []},
            "attachment": [
                {"id": "20001", "filename": "spec.pdf"},
                {"id": "20002", "filename": "diagram.png"},
            ],
            "environment": "Production",
            "customfield_10001": 5,
            "customfield_10020": [
                {"id": 5, "name": "Sprint 12", "state": "active"}
            ],
        },
    }
    issue_2 = {
        "id": "10002",
        "key": "DATA-102",
        "self": f"{BASE_URL}/rest/api/3/issue/10002",
        "fields": {
            "summary": "Tidy the backlog",
            "description": "A plain-string description.",
            "issuetype": {"id": "10002", "name": "Task"},
            "project": {"id": "10000", "key": "DATA", "name": "Data Platform"},
            "status": {
                "id": "10001",
                "name": "To Do",
                "statusCategory": {"id": 2, "name": "To Do"},
            },
            "priority": None,
            "resolution": None,
            "resolutiondate": None,
            "reporter": _user("acc-reporter-1", "Priya Reporter"),
            "assignee": None,
            "creator": _user("acc-reporter-1", "Priya Reporter"),
            "created": "2026-11-02T10:00:00.000+0000",
            "updated": "2026-11-08T11:00:00.000+0000",
            "duedate": None,
            "labels": [],
            "components": [],
            "fixVersions": [],
            "versions": [],
            "comment": {"total": 0, "comments": []},
            "attachment": [],
        },
    }
    issue_3 = {
        "id": "10003",
        "key": "DATA-103",
        "self": f"{BASE_URL}/rest/api/3/issue/10003",
        "fields": {
            "summary": "Add incremental loads",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "Only fetch what changed."}
                        ],
                    }
                ],
            },
            "issuetype": {"id": "10001", "name": "Story"},
            "project": {"id": "10000", "key": "DATA", "name": "Data Platform"},
            "status": {
                "id": "10003",
                "name": "Done",
                "statusCategory": {"id": 3, "name": "Done"},
            },
            "priority": {"id": "3", "name": "Medium"},
            "resolution": {"id": "10000", "name": "Done"},
            "resolutiondate": "2026-11-10T08:00:00.000+0000",
            "reporter": _user("acc-reporter-1", "Priya Reporter"),
            "assignee": _user("acc-assignee-2", "Sam Assignee"),
            "creator": _user("acc-reporter-1", "Priya Reporter"),
            "created": "2026-11-03T09:30:00.000+0000",
            "updated": "2026-11-10T16:45:00.000+0000",
            "duedate": None,
            "labels": ["incremental"],
            "components": [{"id": "10010", "name": "ETL"}],
            "fixVersions": [],
            "versions": [],
            "comment": {"total": 1, "comments": []},
            "attachment": [],
            "customfield_10002": {"value": "High"},
            "customfield_10003": {"displayName": "Olivia Owner"},
        },
    }
    return [issue_1, issue_2, issue_3]


@pytest.fixture
def sample_jira_search_response(sample_issues) -> dict:
    """A realistic ``/search/jql`` response wrapping the 3 fixtures."""
    return {
        "total": 3,
        "startAt": 0,
        "maxResults": 100,
        "isLast": True,
        "issues": sample_issues,
    }


@pytest.fixture
def sample_dataframe(sample_issues) -> pd.DataFrame:
    """The 3 fixture issues transformed with the standard mapping."""
    transformer = JiraTransformer(base_url=BASE_URL, custom_fields=CUSTOM_FIELDS)
    return transformer.transform_issues(sample_issues)


@pytest.fixture
def tmp_dirs(tmp_path) -> dict[str, Path]:
    """Per-test working directories mirroring the project layout."""
    dirs = {
        "root": tmp_path,
        "data": tmp_path / "data",
        "output": tmp_path / "data" / "output",
        "raw": tmp_path / "data" / "raw",
        "metadata": tmp_path / "data" / "metadata",
        "logs": tmp_path / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


@pytest.fixture
def tmp_config(tmp_dirs) -> dict:
    """A minimal but complete pipeline config pointing at tmp dirs."""
    return {
        "jira": {
            "base_url": BASE_URL,
            "auth_type": "basic",
            "timeout": 30,
            "page_size": 100,
            "projects": ["DATA"],
            "fields": ["summary", "description", "status", "assignee"],
            "jql": {"issues": "project = DATA ORDER BY updated DESC"},
        },
        "incremental_load": {
            "enabled": True,
            "timestamp_field": "updated",
            "lookback_minutes": 10,
        },
        "output": {
            "default_format": "csv",
            "csv": {"path": str(tmp_dirs["output"] / "jira_issues.csv")},
        },
        "google_sheets": {
            "enabled": False,
            "spreadsheet_id": "",
            "worksheet_name": "jira_issues",
            "credentials_file": "credentials/service_account.json",
        },
        "logging": {
            "enabled": True,
            "level": "INFO",
            "console_level": "INFO",
            "directory": str(tmp_dirs["logs"]),
            "format": "text",
            "files": {
                "application": "jira_pipeline.log",
                "error": "jira_pipeline_error.log",
                "debug": "jira_pipeline_debug.log",
            },
            "rotation": {
                "enabled": True,
                "max_bytes": 10485760,
                "backup_count": 10,
            },
            "retention": {"enabled": True, "days": 30},
        },
        "raw_data": {"enabled": True},
        "metadata": {"retention_days": 365},
        "datasets": {
            "issues": {
                "enabled": True,
                "endpoint": "issues",
                "jql": "project = DATA",
                "output": "jira_issues.csv",
                "key": "issue_id",
            }
        },
    }
