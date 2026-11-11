"""Tests for the LinkedIn post transformer and date helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.transformers.linkedin_transformer import (
    SCHEMA_COLUMNS,
    LinkedInTransformer,
)
from src.utils.date_utils import format_iso_utc, parse_linkedin_datetime


@pytest.fixture()
def transformer() -> LinkedInTransformer:
    return LinkedInTransformer()


def test_transform_posts_canonical_shape(transformer, all_posts, organization_payload) -> None:
    frame = transformer.transform_posts(all_posts, organization_payload)
    assert list(frame.columns)[: len(SCHEMA_COLUMNS)] == SCHEMA_COLUMNS
    assert len(frame) == 3
    first = frame.iloc[0]
    assert first["post_urn"] == "urn:li:share:111111111"
    assert first["post_url"] == "https://www.linkedin.com/feed/update/urn:li:share:111111111"
    assert first["organization_urn"] == "urn:li:organization:12345678"
    assert first["organization_id"] == "12345678"
    assert first["organization_name"] == "Acme Corp"
    assert first["author_type"] == "organization"
    assert first["text"].startswith("Acme launches")
    assert first["created_at"] == "2026-11-12T00:00:00Z"
    assert first["visibility"] == "PUBLIC"
    assert first["lifecycle_state"] == "PUBLISHED"
    assert first["media_type"] == "IMAGE"
    assert first["media_title"] == "Launch photo"
    assert first["source"] == "LINKEDIN"


def test_transform_article_post(transformer, all_posts) -> None:
    frame = transformer.transform_posts(all_posts)
    second = frame.iloc[1]
    assert second["media_type"] == "ARTICLE"
    assert second["article_url"] == "https://example.com/news/acme"
    assert second["created_at"] == "2026-11-11T00:00:00Z"


def test_transform_post_without_content(transformer, all_posts) -> None:
    frame = transformer.transform_posts(all_posts)
    third = frame.iloc[2]
    assert third["media_type"] == ""
    assert third["text"].startswith("Hiring:")
    assert third["created_at"] == "2026-11-10T00:00:00Z"


def test_transform_empty_list_returns_schema_columns(transformer) -> None:
    frame = transformer.transform_posts([])
    assert list(frame.columns) == SCHEMA_COLUMNS
    assert frame.empty


def test_transform_unknown_fields_do_not_fail(transformer, all_posts) -> None:
    posts = [dict(all_posts[0], someFutureField={"nested": True}, weird=None)]
    frame = transformer.transform_posts(posts)
    assert len(frame) == 1
    assert frame.iloc[0]["post_urn"] == "urn:li:share:111111111"


def test_transform_missing_id_kept_for_dq(transformer) -> None:
    frame = transformer.transform_posts([{"commentary": {"text": "no id"}}])
    assert len(frame) == 1
    assert frame.iloc[0]["post_urn"] == ""


def test_engagement_enrichment(transformer, all_posts, social_actions_payload) -> None:
    engagements = {
        "urn:li:share:111111111": social_actions_payload,
    }
    frame = transformer.transform_posts(all_posts, engagements=engagements)
    assert frame.iloc[0]["likes_count"] == 42
    assert frame.iloc[0]["comments_count"] == 7
    assert pd.isna(frame.iloc[1]["likes_count"])


def test_parse_linkedin_datetime_epoch_ms() -> None:
    parsed = parse_linkedin_datetime(1794355200000)
    assert parsed == datetime(2026, 11, 11, tzinfo=timezone.utc)
    assert format_iso_utc(parsed) == "2026-11-11T00:00:00Z"


def test_parse_linkedin_datetime_epoch_seconds_and_iso() -> None:
    assert parse_linkedin_datetime(1794355200) == datetime(
        2026, 11, 11, tzinfo=timezone.utc
    )
    assert parse_linkedin_datetime("2026-11-11T00:00:00Z") == datetime(
        2026, 11, 11, tzinfo=timezone.utc
    )
    assert parse_linkedin_datetime("1794355200000") == datetime(
        2026, 11, 11, tzinfo=timezone.utc
    )


def test_parse_linkedin_datetime_invalid_returns_none() -> None:
    assert parse_linkedin_datetime(None) is None
    assert parse_linkedin_datetime("") is None
    assert parse_linkedin_datetime("not a date") is None
