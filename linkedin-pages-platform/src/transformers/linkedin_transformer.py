"""LinkedIn post transformer: raw Posts API payloads -> canonical DataFrame.

The Posts API returns posts with epoch-millis timestamps, nested
``commentary`` / ``content`` objects and URN identifiers. This module
flattens them into one row per post. It is deliberately tolerant: only
fields actually present in the payload are populated, unknown fields
never fail the transform, and engagement counts are merged in when the
pipeline enriches posts via the Social Actions API.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.utils.date_utils import format_iso_utc, now_utc, parse_linkedin_datetime
from src.utils.exceptions import TransformationError

# Canonical post columns. ``ingestion_timestamp`` is restamped on every
# run and excluded from upsert change detection (see csv_writer).
SCHEMA_COLUMNS = [
    "post_urn",
    "post_url",
    "organization_urn",
    "organization_id",
    "organization_name",
    "author_type",
    "text",
    "created_at",
    "visibility",
    "lifecycle_state",
    "likes_count",
    "comments_count",
    "shares_count",
    "media_type",
    "media_title",
    "media_url",
    "article_url",
    "source",
    "extraction_timestamp",
    "ingestion_timestamp",
]


def _urn_id(urn: Any) -> str:
    """Trailing id of a URN (``urn:li:organization:123`` -> ``123``)."""
    if urn is None:
        return ""
    return str(urn).split(":")[-1]


def _author_type(author_urn: str) -> str:
    if ":organization:" in author_urn:
        return "organization"
    if ":person:" in author_urn:
        return "person"
    return "unknown"


def _extract_text(post: dict[str, Any]) -> str:
    """Post body text from the Posts API ``commentary`` object."""
    commentary = post.get("commentary")
    if isinstance(commentary, dict):
        text = commentary.get("text")
        if isinstance(text, str):
            return text
    # Legacy UGC shape, kept for tolerance.
    specific = post.get("specificContent") or {}
    share = specific.get("com.linkedin.ugc.ShareContent", {})
    text = (share.get("shareCommentary") or {}).get("text")
    return text if isinstance(text, str) else ""


def _extract_media(post: dict[str, Any]) -> dict[str, str]:
    """Media/article details from the ``content`` object."""
    content = post.get("content") or {}
    media_type, media_title, media_url, article_url = "", "", "", ""
    if not isinstance(content, dict):
        return {
            "media_type": media_type,
            "media_title": media_title,
            "media_url": media_url,
            "article_url": article_url,
        }
    media = content.get("media")
    if isinstance(media, dict):
        media_type = str(media.get("type", "") or media.get("mediaType", ""))
        media_title = str(media.get("title", "") or "")
        media_url = str(media.get("id", "") or media.get("url", "") or "")
    article = content.get("article")
    if isinstance(article, dict):
        article_url = str(article.get("source", "") or article.get("url", "") or "")
        if not media_type:
            media_type = "ARTICLE"
        if not media_title:
            media_title = str(article.get("title", "") or "")
    multi = content.get("multiImage")
    if isinstance(multi, dict) and not media_type:
        media_type = "MULTI_IMAGE"
    return {
        "media_type": media_type,
        "media_title": media_title,
        "media_url": media_url,
        "article_url": article_url,
    }


def _extract_counts(post: dict[str, Any]) -> dict[str, int | None]:
    """Engagement counts when present directly on the post payload."""
    likes = post.get("likesCount", post.get("likeCount"))
    comments = post.get("commentsCount", post.get("commentCount"))
    shares = post.get("sharesCount", post.get("shareCount"))
    return {
        "likes_count": int(likes) if isinstance(likes, (int, float)) else None,
        "comments_count": int(comments) if isinstance(comments, (int, float)) else None,
        "shares_count": int(shares) if isinstance(shares, (int, float)) else None,
    }


class LinkedInTransformer:
    """Transform LinkedIn Posts API payloads into the canonical schema."""

    def transform_post(
        self,
        post: dict[str, Any],
        organization: dict[str, Any] | None = None,
        engagement: dict[str, Any] | None = None,
        extraction_timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Transform one post payload into a canonical record dict."""
        if not isinstance(post, dict):
            raise TransformationError(
                f"Expected a post object, got {type(post).__name__}"
            )
        post_urn = str(post.get("id", "") or "")
        author_urn = str(post.get("author", "") or "")
        org_urn = author_urn
        org_id = _urn_id(author_urn)
        org_name = ""
        if organization:
            org_name = str(
                organization.get("localizedName", "")
                or organization.get("vanityName", "")
                or ""
            )
            if not org_urn:
                org_urn = self.organization_urn(organization)
                org_id = _urn_id(org_urn)
        created = parse_linkedin_datetime(
            post.get("publishedAt", post.get("createdAt", post.get("created")))
        )
        media = _extract_media(post)
        counts = _extract_counts(post)
        if engagement:
            # Social Actions enrichment wins over payload counts.
            likes_summary = engagement.get("likesSummary") or {}
            comments_summary = engagement.get("commentsSummary") or {}
            if isinstance(likes_summary.get("totalLikes"), (int, float)):
                counts["likes_count"] = int(likes_summary["totalLikes"])
            if isinstance(comments_summary.get("totalComments"), (int, float)):
                counts["comments_count"] = int(comments_summary["totalComments"])
            if isinstance(engagement.get("sharesCount"), (int, float)):
                counts["shares_count"] = int(engagement["sharesCount"])
        stamp = extraction_timestamp or format_iso_utc(now_utc())
        return {
            "post_urn": post_urn,
            "post_url": f"https://www.linkedin.com/feed/update/{post_urn}"
            if post_urn
            else "",
            "organization_urn": org_urn,
            "organization_id": org_id,
            "organization_name": org_name,
            "author_type": _author_type(author_urn),
            "text": _extract_text(post),
            "created_at": format_iso_utc(created),
            "visibility": str(post.get("visibility", "") or ""),
            "lifecycle_state": str(post.get("lifecycleState", "") or ""),
            "likes_count": counts["likes_count"],
            "comments_count": counts["comments_count"],
            "shares_count": counts["shares_count"],
            "media_type": media["media_type"],
            "media_title": media["media_title"],
            "media_url": media["media_url"],
            "article_url": media["article_url"],
            "source": "LINKEDIN",
            "extraction_timestamp": stamp,
            "ingestion_timestamp": format_iso_utc(now_utc()),
        }

    @staticmethod
    def organization_urn(organization: dict[str, Any]) -> str:
        """URN for an organisation payload (``id`` or ``vanityName``)."""
        org_id = organization.get("id")
        if org_id is not None:
            return f"urn:li:organization:{org_id}"
        return ""

    def transform_posts(
        self,
        posts: list[dict[str, Any]],
        organization: dict[str, Any] | None = None,
        engagements: dict[str, dict[str, Any]] | None = None,
    ) -> pd.DataFrame:
        """Transform a list of post payloads into the canonical DataFrame.

        ``engagements`` optionally maps ``post_urn -> socialActions`` payloads.
        Unknown fields never fail the transform; rows without a post URN
        are kept (they fail data-quality validation downstream instead).
        """
        engagements = engagements or {}
        stamp = format_iso_utc(now_utc())
        records = [
            self.transform_post(
                post,
                organization=organization,
                engagement=engagements.get(str(post.get("id", "") or "")),
                extraction_timestamp=stamp,
            )
            for post in posts
        ]
        if not records:
            return pd.DataFrame(columns=SCHEMA_COLUMNS)
        frame = pd.DataFrame(records)
        # Preserve any extra keys the payloads carried, after the canonicals.
        return frame.reindex(
            columns=SCHEMA_COLUMNS
            + [c for c in frame.columns if c not in SCHEMA_COLUMNS]
        )
