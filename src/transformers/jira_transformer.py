"""Normalise Jira issue JSON into the standardized tabular schema (§4, §10).

The transformer owns all knowledge of Jira's nested payload shape and
turns it into a clean :class:`pandas.DataFrame`. Missing fields are
gracefully handled — a field that does not exist in Jira becomes an
empty value, never an exception.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.utils.date_utils import format_iso_utc, now_utc, parse_jira_datetime
from src.utils.logger import get_logger

logger = get_logger("jira_pipeline.transformer")

# Standard issue-level schema (§4). Column order is the output order.
SCHEMA_COLUMNS: list[str] = [
    "issue_id",
    "issue_key",
    "project_id",
    "project_key",
    "project_name",
    "summary",
    "description",
    "issue_type",
    "issue_type_id",
    "status",
    "status_id",
    "status_category",
    "priority",
    "priority_id",
    "resolution",
    "resolution_date",
    "reporter",
    "reporter_account_id",
    "assignee",
    "assignee_account_id",
    "creator",
    "creator_account_id",
    "created",
    "updated",
    "due_date",
    "labels",
    "components",
    "fix_versions",
    "affected_versions",
    "parent_issue_key",
    "epic_key",
    "sprint",
    "story_points",
    "environment",
    "comment_count",
    "attachment_count",
    "url",
    "ingestion_timestamp",
]

# Jira custom-field ids commonly used for sprint / epic link, checked
# when the mapped custom-field config does not already cover them.
SPRINT_FIELD_IDS = ("customfield_10020", "customfield_10007")
EPIC_LINK_FIELD_IDS = ("customfield_10014", "customfield_10008")


class JiraTransformer:
    """Convert raw Jira issue payloads into the standardized DataFrame.

    Args:
        base_url: Jira site URL, used to build the per-issue ``url``.
        custom_fields: Mapping of Jira custom-field id to output column
            name, e.g. ``{"customfield_10001": "story_points"}``.
            Loaded from ``config/jira_fields.yaml`` (§5).
        retain_unknown_custom_fields: When True, custom fields not in
            the mapping are kept as extra ``custom_<id>`` columns.
    """

    def __init__(
        self,
        base_url: str = "",
        custom_fields: dict[str, str] | None = None,
        retain_unknown_custom_fields: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.custom_fields = custom_fields or {}
        self.retain_unknown_custom_fields = retain_unknown_custom_fields

    # -- public API ---------------------------------------------------------
    def transform_issues(self, issues: list[dict[str, Any]]) -> pd.DataFrame:
        """Transform a list of raw Jira issue dicts into a DataFrame."""
        rows = [self.flatten_issue(issue) for issue in issues]
        df = pd.DataFrame(rows, columns=self._output_columns())
        df = self.normalize_dates(df)
        df = self.normalize_users(df)
        df = self.normalize_lists(df)
        df = self.normalize_custom_fields(df)
        return df

    def flatten_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        """Flatten one raw Jira issue dict into a single schema row."""
        fields: dict[str, Any] = issue.get("fields") or {}
        project = fields.get("project") or {}
        issue_type = fields.get("issuetype") or {}
        status = fields.get("status") or {}
        status_category = status.get("statusCategory") or {}
        priority = fields.get("priority") or {}
        resolution = fields.get("resolution") or {}
        parent = fields.get("parent") or {}
        epic = fields.get("epic") or {}
        comment = fields.get("comment") or {}
        attachments = fields.get("attachment") or []

        row: dict[str, Any] = {
            "issue_id": str(issue.get("id")) if issue.get("id") is not None else None,
            "issue_key": issue.get("key"),
            "project_id": (
                str(project.get("id")) if project.get("id") is not None else None
            ),
            "project_key": project.get("key"),
            "project_name": project.get("name"),
            "summary": fields.get("summary"),
            "description": self._description_text(fields.get("description")),
            "issue_type": issue_type.get("name"),
            "issue_type_id": (
                str(issue_type.get("id")) if issue_type.get("id") is not None else None
            ),
            "status": status.get("name"),
            "status_id": (
                str(status.get("id")) if status.get("id") is not None else None
            ),
            "status_category": status_category.get("name"),
            "priority": priority.get("name"),
            "priority_id": (
                str(priority.get("id")) if priority.get("id") is not None else None
            ),
            "resolution": resolution.get("name"),
            "resolution_date": self._iso(fields.get("resolutiondate")),
            "reporter": self._user_name(fields.get("reporter")),
            "reporter_account_id": self._user_account(fields.get("reporter")),
            "assignee": self._user_name(fields.get("assignee")),
            "assignee_account_id": self._user_account(fields.get("assignee")),
            "creator": self._user_name(fields.get("creator")),
            "creator_account_id": self._user_account(fields.get("creator")),
            "created": self._iso(fields.get("created")),
            "updated": self._iso(fields.get("updated")),
            "due_date": self._iso(fields.get("duedate")),
            "labels": fields.get("labels") or [],
            "components": self._names(fields.get("components")),
            "fix_versions": self._names(fields.get("fixVersions")),
            "affected_versions": self._names(fields.get("versions")),
            "parent_issue_key": parent.get("key"),
            "epic_key": epic.get("key") or self._epic_link(fields),
            "sprint": self._sprint_name(fields),
            "story_points": self._mapped_value(fields, "story_points"),
            "environment": fields.get("environment"),
            "comment_count": (
                int(comment.get("total", 0)) if isinstance(comment, dict) else 0
            ),
            "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
            "url": (
                f"{self.base_url}/browse/{issue.get('key')}"
                if self.base_url and issue.get("key")
                else None
            ),
            "ingestion_timestamp": format_iso_utc(now_utc()),
        }

        # Mapped custom fields beyond the standard columns.
        for field_id, column in self.custom_fields.items():
            if column in row:
                continue  # already handled (e.g. story_points)
            row[column] = self._custom_value(fields.get(field_id))

        # Optionally retain unmapped custom fields as custom_<id>.
        if self.retain_unknown_custom_fields:
            for field_id, value in fields.items():
                if (
                    field_id.startswith("customfield_")
                    and field_id not in self.custom_fields
                ):
                    row[f"custom_{field_id}"] = self._custom_value(value)

        return row

    def normalize_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Parse date columns to UTC datetimes (unparseable → NaT)."""
        for column in ("created", "updated", "resolution_date", "due_date"):
            if column in df.columns:
                df[column] = pd.to_datetime(df[column], utc=True, errors="coerce")
        return df

    def normalize_users(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure user display names are plain strings (never dicts)."""
        for column in ("reporter", "assignee", "creator"):
            if column in df.columns:
                df[column] = df[column].apply(
                    lambda value: (
                        value.get("displayName") if isinstance(value, dict) else value
                    )
                )
        return df

    def normalize_lists(self, df: pd.DataFrame) -> pd.DataFrame:
        """Join list columns into ``";"``-separated strings."""
        for column in ("labels", "components", "fix_versions", "affected_versions"):
            if column in df.columns:
                df[column] = df[column].apply(self._join_list)
        return df

    def normalize_custom_fields(self, df: pd.DataFrame) -> pd.DataFrame:
        """Coerce mapped custom-field columns to plain scalars where possible."""
        for column in self.custom_fields.values():
            if column in df.columns:
                df[column] = df[column].apply(self._custom_value)
        return df

    # -- helpers ------------------------------------------------------------
    def _output_columns(self) -> list[str]:
        columns = list(SCHEMA_COLUMNS)
        for column in self.custom_fields.values():
            if column not in columns:
                columns.append(column)
        return columns

    @staticmethod
    def _iso(value: Any) -> str | None:
        parsed = parse_jira_datetime(value)
        return format_iso_utc(parsed)

    @staticmethod
    def _user_name(user: Any) -> str | None:
        if isinstance(user, dict):
            return user.get("displayName") or user.get("name") or user.get("emailAddress")
        return user if isinstance(user, str) else None

    @staticmethod
    def _user_account(user: Any) -> str | None:
        if isinstance(user, dict):
            return user.get("accountId")
        return None

    @staticmethod
    def _names(items: Any) -> list[str]:
        if not isinstance(items, list):
            return []
        return [item.get("name") for item in items if isinstance(item, dict) and item.get("name")]

    @staticmethod
    def _join_list(value: Any) -> str:
        if isinstance(value, list):
            return ";".join(str(item) for item in value if item is not None)
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _description_text(value: Any) -> str | None:
        """Jira Cloud descriptions are Atlassian Document Format (ADF).

        Extract the plain text from ADF paragraphs; fall back to the raw
        string for Server instances.
        """
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and value.get("type") == "doc":
            parts: list[str] = []
            for node in value.get("content", []):
                for child in node.get("content", []):
                    if isinstance(child, dict) and child.get("text"):
                        parts.append(child["text"])
            return " ".join(parts) if parts else None
        return str(value)

    def _mapped_value(self, fields: dict[str, Any], column: str) -> Any:
        """Value of the custom field mapped to ``column`` (or None)."""
        for field_id, name in self.custom_fields.items():
            if name == column:
                return self._custom_value(fields.get(field_id))
        return None

    @staticmethod
    def _custom_value(value: Any) -> Any:
        """Reduce a custom-field value to a scalar when possible."""
        if value is None:
            return None
        if isinstance(value, dict):
            if "value" in value:
                return value["value"]
            if "name" in value:
                return value["name"]
            if "displayName" in value:
                return value["displayName"]
            return None
        if isinstance(value, list):
            if not value:
                return None
            if all(isinstance(item, dict) for item in value):
                names = [
                    item.get("name") or item.get("value")
                    for item in value
                    if item.get("name") or item.get("value")
                ]
                return ";".join(str(name) for name in names) if names else None
            return ";".join(str(item) for item in value)
        return value

    def _sprint_name(self, fields: dict[str, Any]) -> str | None:
        sprint = fields.get("sprint")
        if sprint is None:
            for field_id in SPRINT_FIELD_IDS:
                sprint = fields.get(field_id)
                if sprint:
                    break
        if isinstance(sprint, dict):
            return sprint.get("name")
        if isinstance(sprint, list) and sprint:
            last = sprint[-1]
            if isinstance(last, dict):
                return last.get("name")
        return None

    def _epic_link(self, fields: dict[str, Any]) -> str | None:
        for field_id in EPIC_LINK_FIELD_IDS:
            value = fields.get(field_id)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                return value.get("key")
        return None
