"""End-to-end acceptance tests (spec §28, final acceptance criteria).

Every test runs against a mocked Jira HTTP layer — no live Jira is
required. The five acceptance tests:

1. Full CSV extraction: Jira API -> transform -> CSV.
2. Second execution: upsert appends new rows, updates changed rows,
   and creates no duplicates.
3. Incremental execution: watermark JQL, upsert, watermark advanced.
4. Google Sheets: upsert against a mocked gspread client.
5. Failure recovery: timeout, 429, 500, invalid JQL, auth failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import tenacity

from src.clients.jira_client import JiraClient
from src.handlers.jira_handler import JiraHandler
from src.pipeline.jira_pipeline import JiraPipeline
from src.transformers.jira_transformer import JiraTransformer
from src.utils.exceptions import InvalidJQLError, JiraAuthError
from src.writers.csv_writer import CSVWriter
from src.writers.metadata_writer import MetadataWriter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _issue(
    key: str,
    summary: str,
    updated: str,
    points: int | None = None,
    assignee: str | None = "Alice Example",
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": summary,
        "description": f"Description for {key}",
        "issuetype": {"id": "10001", "name": "Task"},
        "status": {
            "id": "3",
            "name": "Done",
            "statusCategory": {"name": "Done"},
        },
        "priority": {"id": "3", "name": "Medium"},
        "reporter": {"displayName": "Bob Reporter", "accountId": "acc-bob"},
        "assignee": (
            {"displayName": assignee, "accountId": "acc-alice"}
            if assignee
            else None
        ),
        "creator": {"displayName": "Bob Reporter", "accountId": "acc-bob"},
        "created": "2026-11-01T09:00:00.000+0000",
        "updated": updated,
        "labels": ["ingestion"],
        "components": [{"name": "Platform"}],
        "fixVersions": [],
        "versions": [],
        "comment": {"total": 2},
        "attachment": [{"id": "1"}],
        "project": {"id": "10000", "key": "DATA", "name": "Data Platform"},
    }
    if points is not None:
        fields["customfield_10001"] = points
    return {"id": str(1000 + int(key.split("-")[1])), "key": key, "fields": fields}


@pytest.fixture()
def issues_page_1() -> list[dict[str, Any]]:
    return [
        _issue("DATA-1", "First issue", "2026-11-09T10:00:00.000+0000", points=3),
        _issue("DATA-2", "Second issue", "2026-11-09T11:00:00.000+0000", points=5),
    ]


@pytest.fixture()
def issues_page_2() -> list[dict[str, Any]]:
    return [_issue("DATA-3", "Third issue", "2026-11-09T12:00:00.000+0000")]


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tenacity retries instant in tests."""
    monkeypatch.setattr(
        tenacity, "wait_exponential", lambda *a, **k: tenacity.wait_none()
    )


def _make_client(monkeypatch: pytest.MonkeyPatch, handler_fn) -> JiraClient:
    _no_wait(monkeypatch)
    client = JiraClient(
        base_url="https://test.atlassian.net",
        username="test@example.com",
        api_token="test-token",
        max_retries=2,
    )
    monkeypatch.setattr(client.session, "request", handler_fn)
    return client


def _make_pipeline(
    tmp_path: Path,
    client: JiraClient,
    config_overrides: dict[str, Any] | None = None,
    sheets_writer=None,
) -> JiraPipeline:
    data_dir = tmp_path / "data"
    config: dict[str, Any] = {
        "jira": {
            "base_url": "https://test.atlassian.net",
            "fields": ["summary", "status", "assignee", "created", "updated"],
            "jql": {"issues": "project = DATA ORDER BY updated DESC"},
            "projects": ["DATA"],
            "page_size": 2,
        },
        "incremental_load": {"enabled": True, "lookback_minutes": 10},
        "output": {"csv": {"path": str(data_dir / "output" / "jira_issues.csv")}},
        "google_sheets": {"enabled": False},
        "logging": {"enabled": True, "level": "INFO", "directory": str(tmp_path / "logs")},
        "raw_data": {"enabled": True},
        "metadata": {"retention_days": 365},
    }
    if config_overrides:
        config.update(config_overrides)
    transformer = JiraTransformer(
        base_url="https://test.atlassian.net",
        custom_fields={"customfield_10001": "story_points"},
    )
    handler = JiraHandler(
        base_url="https://test.atlassian.net",
        client=client,
        transformer=transformer,
        page_size=2,
    )
    return JiraPipeline(
        config=config,
        handler=handler,
        transformer=transformer,
        csv_writer=CSVWriter(),
        sheets_writer=sheets_writer,
        metadata_writer=MetadataWriter(
            metadata_dir=data_dir / "metadata",
            output_metadata_path=data_dir / "output" / "metadata.json",
        ),
        project_root=tmp_path,
    )


def _search_router(pages: list[list[dict[str, Any]]]):
    """Route mocked HTTP calls: /myself + paged /search/jql."""
    calls: list[dict[str, Any]] = []

    def _handler(method, url, params=None, json=None, timeout=None, **kwargs):
        calls.append({"method": method, "url": url, "json": json})
        if url.endswith("/myself"):
            return FakeResponse(
                200, {"displayName": "Test User", "emailAddress": "test@example.com"}
            )
        if "search/jql" in url:
            start_at = (json or {}).get("startAt", 0)
            page_index = start_at // 2
            page = pages[page_index] if page_index < len(pages) else []
            total = sum(len(p) for p in pages)
            return FakeResponse(
                200, {"total": total, "issues": page, "startAt": start_at}
            )
        return FakeResponse(404, text="not found")

    _handler.calls = calls  # type: ignore[attr-defined]
    return _handler


# ---------------------------------------------------------------------------
# Acceptance test 1 — full CSV extraction
# ---------------------------------------------------------------------------
def test_acceptance_1_full_csv_extraction(
    tmp_path, monkeypatch, issues_page_1, issues_page_2
):
    router = _search_router([issues_page_1, issues_page_2])
    client = _make_client(monkeypatch, router)
    pipeline = _make_pipeline(tmp_path, client)
    metadata = pipeline.run_full_load()

    assert metadata.status == "SUCCESS"
    assert metadata.records_extracted == 3
    csv_path = tmp_path / "data" / "output" / "jira_issues.csv"
    df = pd.read_csv(csv_path)
    assert len(df) == 3
    assert set(df["issue_key"]) == {"DATA-1", "DATA-2", "DATA-3"}
    assert df.loc[df["issue_key"] == "DATA-1", "story_points"].iloc[0] == 3
    raw_files = list((tmp_path / "data" / "raw" / "jira").rglob("*.json"))
    assert len(raw_files) == 1


# ---------------------------------------------------------------------------
# Acceptance test 2 — second run upserts without duplicates
# ---------------------------------------------------------------------------
def test_acceptance_2_second_run_upsert_no_duplicates(
    tmp_path, monkeypatch, issues_page_1, issues_page_2
):
    router = _search_router([issues_page_1, issues_page_2])
    client = _make_client(monkeypatch, router)
    pipeline = _make_pipeline(tmp_path, client)
    first = pipeline.run_full_load()
    assert first.status == "SUCCESS"

    # Second run: DATA-2 changed, DATA-4 is new, DATA-1/3 unchanged.
    updated_pages = [
        [
            _issue("DATA-1", "First issue", "2026-11-09T10:00:00.000+0000", points=3),
            _issue("DATA-2", "Second issue UPDATED", "2026-11-10T09:00:00.000+0000", points=8),
        ],
        [
            _issue("DATA-3", "Third issue", "2026-11-09T12:00:00.000+0000"),
            _issue("DATA-4", "Fourth issue", "2026-11-10T09:30:00.000+0000", points=2),
        ],
    ]
    router2 = _search_router(updated_pages)
    client2 = _make_client(monkeypatch, router2)
    pipeline2 = _make_pipeline(tmp_path, client2)
    second = pipeline2.run_full_load()

    assert second.status == "SUCCESS"
    assert second.records_inserted == 1
    assert second.records_updated == 1
    df = pd.read_csv(tmp_path / "data" / "output" / "jira_issues.csv")
    assert len(df) == 4  # no duplicates
    assert df["issue_key"].is_unique
    assert df.loc[df["issue_key"] == "DATA-2", "summary"].iloc[0] == "Second issue UPDATED"
    assert df.loc[df["issue_key"] == "DATA-2", "story_points"].iloc[0] == 8


# ---------------------------------------------------------------------------
# Acceptance test 3 — incremental run + watermark
# ---------------------------------------------------------------------------
def test_acceptance_3_incremental_watermark(tmp_path, monkeypatch):
    # Seed a successful run watermark: 2026-11-09T12:00:00Z.
    metadata_dir = tmp_path / "data" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "latest_run.json").write_text(
        json.dumps(
            {
                "pipeline_run_id": "20261109_120000_ab12",
                "status": "SUCCESS",
                "current_watermark": "2026-11-09T12:00:00Z",
                "last_successful_run": "2026-11-09T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    new_issues = [_issue("DATA-5", "Fifth issue", "2026-11-10T08:00:00.000+0000", points=1)]
    router = _search_router([new_issues])
    client = _make_client(monkeypatch, router)
    pipeline = _make_pipeline(tmp_path, client)
    metadata = pipeline.run_incremental_load()

    assert metadata.status == "SUCCESS"
    assert metadata.records_extracted == 1
    # The JQL sent to Jira must carry the watermark (minus lookback).
    sent_jql = router.calls[-1]["json"]["jql"]
    assert 'updated >= "2026-11-09 11:50"' in sent_jql
    assert "ORDER BY updated ASC" in sent_jql
    # Watermark advanced to the newest updated value.
    assert metadata.current_watermark == "2026-11-10T08:00:00Z"
    output_meta = json.loads(
        (tmp_path / "data" / "output" / "metadata.json").read_text(encoding="utf-8")
    )
    assert output_meta["status"] == "success"
    assert output_meta["records_processed"] == 1


# ---------------------------------------------------------------------------
# Acceptance test 4 — Google Sheets upsert (mocked gspread)
# ---------------------------------------------------------------------------
class _FakeWorksheet:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.append_calls = 0
        self.update_calls: list[tuple[str, list[list[str]]]] = []

    def get_all_values(self):
        return [list(row) for row in self.rows]

    def append_rows(self, rows, value_input_option=None):
        self.rows.extend([list(r) for r in rows])
        self.append_calls += 1

    def update(self, range_name, values, value_input_option=None):
        self.update_calls.append((range_name, values))
        # Apply the update to the stored rows (range like A2:AL2).
        import re

        row_no = int(re.search(r"(\d+)", range_name).group(1))
        for offset, row in enumerate(values):
            self.rows[row_no - 1 + offset] = list(row)

    def clear(self):
        self.rows = []


class _FakeSpreadsheet:
    def __init__(self) -> None:
        self._sheets: dict[str, _FakeWorksheet] = {}

    def worksheet(self, title):
        if title not in self._sheets:
            raise KeyError(title)
        return self._sheets[title]

    def add_worksheet(self, title, rows, cols):
        sheet = _FakeWorksheet()
        self._sheets[title] = sheet
        return sheet


class _FakeSheetsClient:
    def __init__(self) -> None:
        self.spreadsheet = _FakeSpreadsheet()
        self.authorized = False

    def authorize(self):
        self.authorized = True
        return self

    def open_spreadsheet(self, spreadsheet_id):
        return self.spreadsheet

    def get_or_create_worksheet(self, spreadsheet, title, rows=1000, cols=40):
        try:
            return spreadsheet.worksheet(title)
        except KeyError:
            return spreadsheet.add_worksheet(title, rows, cols)

    def read_all_values(self, worksheet):
        return worksheet.get_all_values()

    def append_rows(self, worksheet, rows, chunk_size=100):
        worksheet.append_rows(rows)
        return len(rows)

    def update_range(self, worksheet, range_name, values):
        worksheet.update(range_name, values)


def test_acceptance_4_google_sheets_upsert(tmp_path, monkeypatch):
    from src.writers.google_sheet_writer import GoogleSheetWriter

    router = _search_router(
        [[_issue("DATA-1", "First issue", "2026-11-09T10:00:00.000+0000", points=3)]]
    )
    client = _make_client(monkeypatch, router)
    fake_client = _FakeSheetsClient()
    sheets_writer = GoogleSheetWriter(
        spreadsheet_id="fake-spreadsheet-id",
        worksheet_name="jira_issues",
        client=fake_client,
    )
    pipeline = _make_pipeline(
        tmp_path,
        client,
        config_overrides={"google_sheets": {"enabled": True}},
        sheets_writer=sheets_writer,
    )
    metadata = pipeline.run(mode="full", outputs=["csv", "google_sheets"])
    assert metadata.status == "SUCCESS"
    assert metadata.google_sheets_status == "SUCCESS"

    worksheet = fake_client.spreadsheet.worksheet("jira_issues")
    assert worksheet.rows[0][0] == "issue_id"  # header written
    assert any("DATA-1" in row for row in worksheet.rows)

    # Second run with a changed summary: sheet row updated in place.
    router2 = _search_router(
        [[_issue("DATA-1", "First issue UPDATED", "2026-11-10T09:00:00.000+0000", points=3)]]
    )
    client2 = _make_client(monkeypatch, router2)
    pipeline2 = _make_pipeline(
        tmp_path,
        client2,
        config_overrides={"google_sheets": {"enabled": True}},
        sheets_writer=sheets_writer,
    )
    metadata2 = pipeline2.run(mode="full", outputs=["csv", "google_sheets"])
    assert metadata2.status == "SUCCESS"
    assert len(worksheet.rows) == 2  # header + 1 row, no duplicate
    assert "First issue UPDATED" in worksheet.rows[1]


# ---------------------------------------------------------------------------
# Acceptance test 5 — failure recovery
# ---------------------------------------------------------------------------
def _failing_router(script):
    """script: list of FakeResponse objects or Exceptions, consumed in order."""
    state = {"index": 0}

    def _handler(method, url, params=None, json=None, timeout=None, **kwargs):
        if url.endswith("/myself"):
            return FakeResponse(
                200, {"displayName": "Test User", "emailAddress": "test@example.com"}
            )
        item = script[min(state["index"], len(script) - 1)]
        state["index"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    _handler.state = state  # type: ignore[attr-defined]
    return _handler


def test_acceptance_5a_rate_limit_then_success(tmp_path, monkeypatch):
    import requests

    script = [
        FakeResponse(429, text="rate limited", headers={"Retry-After": "1"}),
        FakeResponse(
            200,
            {
                "total": 1,
                "issues": [_issue("DATA-1", "First issue", "2026-11-09T10:00:00.000+0000")],
                "startAt": 0,
            },
        ),
    ]
    router = _failing_router(script)
    client = _make_client(monkeypatch, router)
    pipeline = _make_pipeline(tmp_path, client)
    metadata = pipeline.run_full_load()
    assert metadata.status == "SUCCESS"
    assert metadata.records_extracted == 1
    assert client.retry_count >= 1


def test_acceptance_5b_server_error_then_success(tmp_path, monkeypatch):
    script = [
        FakeResponse(500, text="server error"),
        FakeResponse(
            200,
            {
                "total": 1,
                "issues": [_issue("DATA-1", "First issue", "2026-11-09T10:00:00.000+0000")],
                "startAt": 0,
            },
        ),
    ]
    client = _make_client(monkeypatch, _failing_router(script))
    pipeline = _make_pipeline(tmp_path, client)
    metadata = pipeline.run_full_load()
    assert metadata.status == "SUCCESS"
    assert metadata.records_extracted == 1


def test_acceptance_5c_timeout_then_success(tmp_path, monkeypatch):
    import requests

    script = [
        requests.exceptions.Timeout("timed out"),
        FakeResponse(
            200,
            {
                "total": 1,
                "issues": [_issue("DATA-1", "First issue", "2026-11-09T10:00:00.000+0000")],
                "startAt": 0,
            },
        ),
    ]
    client = _make_client(monkeypatch, _failing_router(script))
    pipeline = _make_pipeline(tmp_path, client)
    metadata = pipeline.run_full_load()
    assert metadata.status == "SUCCESS"


def test_acceptance_5d_invalid_jql_not_retried(tmp_path, monkeypatch):
    script = [FakeResponse(400, text="Error in JQL query")]
    router = _failing_router(script)
    client = _make_client(monkeypatch, router)
    pipeline = _make_pipeline(tmp_path, client)
    with pytest.raises(InvalidJQLError):
        client.search_jql("project = DATA AND badfield = 1")
    assert router.state["index"] == 1  # exactly one attempt, no retry
    metadata = pipeline.run_full_load()
    assert metadata.status == "FAILED"
    assert metadata.error["type"] == "InvalidJQLError"


def test_acceptance_5e_auth_failure_not_retried_and_watermark_preserved(
    tmp_path, monkeypatch
):
    # Seed a good watermark first.
    metadata_dir = tmp_path / "data" / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "latest_run.json").write_text(
        json.dumps(
            {
                "pipeline_run_id": "20261109_120000_ab12",
                "status": "SUCCESS",
                "current_watermark": "2026-11-09T12:00:00Z",
                "last_successful_run": "2026-11-09T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    calls = {"count": 0}

    def _handler(method, url, params=None, json=None, timeout=None, **kwargs):
        calls["count"] += 1
        return FakeResponse(401, text="unauthorized")

    client = _make_client(monkeypatch, _handler)
    pipeline = _make_pipeline(tmp_path, client)
    metadata = pipeline.run_full_load()
    assert metadata.status == "FAILED"
    assert metadata.error["type"] == "JiraAuthError"
    assert calls["count"] == 1  # 401 never retried
    # Watermark preserved: latest_run.json still holds the old success.
    latest = json.loads(
        (metadata_dir / "latest_run.json").read_text(encoding="utf-8")
    )
    assert latest["current_watermark"] == "2026-11-09T12:00:00Z"
    # Failed run is still recorded in the append-only history.
    runs = (metadata_dir / "pipeline_runs.json").read_text(encoding="utf-8").strip().splitlines()
    assert len(runs) == 1
    assert json.loads(runs[0])["status"] == "FAILED"
