"""Tests for the LinkedIn Pages handler — client stubbed, no network."""

from __future__ import annotations

from typing import Any

import pytest

from src.handlers.linkedin_handler import LinkedInPagesHandler
from src.utils.exceptions import ConfigError, LinkedInAPIError


class StubClient:
    """Minimal stand-in for LinkedInClient backed by fixtures."""

    def __init__(self, fixtures: dict[str, Any]) -> None:
        self.fixtures = fixtures
        self.api_version = "202610"
        self.list_posts_calls: list[dict[str, Any]] = []

    def get_me(self) -> dict[str, Any]:
        return self.fixtures["member"]

    def list_organization_acls(self) -> list[dict[str, Any]]:
        return self.fixtures["acls"]["elements"]

    def get_organization(self, organization_id: str | int) -> dict[str, Any]:
        return self.fixtures["organization"]

    def list_posts(
        self, author_urn: str, count: int = 10, start: int = 0, sort_by: str = "CREATED"
    ) -> dict[str, Any]:
        self.list_posts_calls.append({"author": author_urn, "count": count, "start": start})
        if start == 0:
            return self.fixtures["posts_page_1"]
        if start == 2:
            return self.fixtures["posts_page_2"]
        return {"elements": [], "paging": {"count": 0, "start": start, "total": 3}}

    def get_post(self, post_urn: str) -> dict[str, Any]:
        return self.fixtures["posts_page_1"]["elements"][0]

    def get_post_social_actions(self, post_urn: str) -> dict[str, Any]:
        if post_urn == "urn:li:share:404":
            raise LinkedInAPIError("not found", status_code=404)
        return self.fixtures["social_actions"]

    def get_organization_share_statistics(self, organization_urn: str) -> dict[str, Any]:
        return self.fixtures["share_statistics"]


@pytest.fixture()
def handler(member_payload, organization_acls_payload, organization_payload,
             posts_page_1, posts_page_2, social_actions_payload,
             share_statistics_payload) -> LinkedInPagesHandler:
    stub = StubClient(
        {
            "member": member_payload,
            "acls": organization_acls_payload,
            "organization": organization_payload,
            "posts_page_1": posts_page_1,
            "posts_page_2": posts_page_2,
            "social_actions": social_actions_payload,
            "share_statistics": share_statistics_payload,
        }
    )
    return LinkedInPagesHandler(client=stub)


def test_handler_requires_token_without_client() -> None:
    with pytest.raises(ConfigError):
        LinkedInPagesHandler(access_token=None)


def test_test_connection(handler: LinkedInPagesHandler) -> None:
    result = handler.test_connection()
    assert result["member"]["id"] == "abcd1234"
    assert result["administered_pages"] == 2  # MEMBER role filtered out
    assert {p["organization_id"] for p in result["pages"]} == {"12345678", "87654321"}


def test_get_administered_pages_filters_roles(handler: LinkedInPagesHandler) -> None:
    pages = handler.get_administered_pages()
    assert all(p["role"] in ("ADMINISTRATOR", "CONTENT_ADMIN") for p in pages)
    assert not any(p["organization_id"] == "99999999" for p in pages)


def test_organization_urn_normalisation() -> None:
    assert LinkedInPagesHandler.organization_urn("123") == "urn:li:organization:123"
    assert (
        LinkedInPagesHandler.organization_urn("urn:li:organization:123")
        == "urn:li:organization:123"
    )


def test_list_all_posts_paginates(handler: LinkedInPagesHandler) -> None:
    posts = handler.list_all_posts("urn:li:organization:12345678", page_size=2)
    assert len(posts) == 3
    assert [p["id"] for p in posts] == [
        "urn:li:share:111111111",
        "urn:li:share:222222222",
        "urn:li:share:333333333",
    ]


def test_list_all_posts_respects_max_posts(handler: LinkedInPagesHandler) -> None:
    posts = handler.list_all_posts(
        "urn:li:organization:12345678", max_posts=2, page_size=2
    )
    assert len(posts) == 2


def test_posts_to_dataframe(handler: LinkedInPagesHandler, all_posts) -> None:
    frame = handler.posts_to_dataframe(all_posts)
    assert len(frame) == 3
    assert list(frame["post_urn"]) == [
        "urn:li:share:111111111",
        "urn:li:share:222222222",
        "urn:li:share:333333333",
    ]
    assert frame.iloc[0]["created_at"] == "2026-11-12T00:00:00Z"


def test_organization_posts_to_dataframe(handler: LinkedInPagesHandler) -> None:
    frame = handler.organization_posts_to_dataframe("urn:li:organization:12345678")
    assert len(frame) == 3
    assert frame.iloc[0]["organization_name"] == "Acme Corp"


def test_get_post_engagement_missing_returns_empty(handler: LinkedInPagesHandler) -> None:
    assert handler.get_post_engagement("urn:li:share:404") == {}
    assert handler.get_post_engagement("urn:li:share:111111111")["likesSummary"]["totalLikes"] == 42
