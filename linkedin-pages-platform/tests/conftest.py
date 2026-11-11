"""Shared fixtures for the LinkedIn Pages pipeline tests.

All payloads are fictional (Acme Corp, Globex Corporation) and shaped
like the real LinkedIn versioned REST API responses. No test here touches
the network or needs a LinkedIn account — the HTTP layer is mocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict | list:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture()
def member_payload() -> dict:
    return load_fixture("member.json")  # type: ignore[return-value]


@pytest.fixture()
def organization_acls_payload() -> dict:
    return load_fixture("organization_acls.json")  # type: ignore[return-value]


@pytest.fixture()
def organization_payload() -> dict:
    return load_fixture("organization.json")  # type: ignore[return-value]


@pytest.fixture()
def posts_page_1() -> dict:
    return load_fixture("posts_page_1.json")  # type: ignore[return-value]


@pytest.fixture()
def posts_page_2() -> dict:
    return load_fixture("posts_page_2.json")  # type: ignore[return-value]


@pytest.fixture()
def social_actions_payload() -> dict:
    return load_fixture("social_actions.json")  # type: ignore[return-value]


@pytest.fixture()
def share_statistics_payload() -> dict:
    return load_fixture("share_statistics.json")  # type: ignore[return-value]


@pytest.fixture()
def all_posts(posts_page_1: dict, posts_page_2: dict) -> list:
    return posts_page_1["elements"] + posts_page_2["elements"]


@pytest.fixture()
def base_config(tmp_path: Path) -> dict:
    """Minimal pipeline config writing into a temp directory."""
    return {
        "environment": "TEST",
        "linkedin": {
            "api_version": "202610",
            "timeout": 30,
            "max_posts": 500,
            "page_size": 2,
            "enrich_engagement": False,
            "incremental_lookback_minutes": 10,
            "organizations": ["urn:li:organization:12345678"],
            "output_csv": str(tmp_path / "output" / "linkedin_posts.csv"),
        },
        "logging": {"enabled": False},
        "paths": {
            "raw_dir": str(tmp_path / "raw"),
            "metadata_dir": str(tmp_path / "metadata"),
            "output_metadata": str(tmp_path / "output" / "metadata.json"),
        },
        "google_sheets": {"enabled": False},
    }
