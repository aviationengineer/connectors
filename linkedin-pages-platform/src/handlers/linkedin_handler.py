"""LinkedIn Pages handler — the reusable entry point for the pipeline.

The handler hides the LinkedIn REST API from downstream code: callers
work with organisations, posts and Pandas DataFrames, never with
Rest.li paths, version headers or pagination cursors.

Access model (enforced by LinkedIn, not by this code)
----------------------------------------------------
Only Company Pages where the authenticated member holds an admin role
(ADMINISTRATOR / CONTENT_ADMIN / DIRECT_SPONSORED_CONTENT_POSTER) can
be read. The LinkedIn app must be approved for the Community Management
API product, with scopes ``r_organization_social`` (read posts),
``rw_organization_admin`` (discover Pages, read analytics) and
``r_organization_social_feed`` (read comments/reactions).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.clients.linkedin_client import LinkedInClient
from src.transformers.linkedin_transformer import LinkedInTransformer
from src.utils.exceptions import ConfigError, LinkedInAPIError
from src.utils.logger import get_logger

logger = get_logger("linkedin_pipeline.linkedin_handler")

ADMIN_ROLES = {"ADMINISTRATOR", "CONTENT_ADMIN", "DIRECT_SPONSORED_CONTENT_POSTER"}


class LinkedInPagesHandler:
    """High-level access to LinkedIn Company Pages via the official API.

    Args:
        access_token: OAuth 2.0 bearer token for the authenticated member.
        api_version: LinkedIn API version, e.g. ``"202610"``.
        timeout: Per-request timeout in seconds.
        client: Optional injected client (dependency injection for tests).
    """

    def __init__(
        self,
        access_token: str | None = None,
        api_version: str | None = None,
        timeout: int = 30,
        client: LinkedInClient | None = None,
    ) -> None:
        if client is not None:
            self.client = client
        else:
            if not access_token:
                raise ConfigError(
                    "No LinkedIn access token. Run "
                    "scripts/linkedin_auth.py first, or set "
                    "LINKEDIN_ACCESS_TOKEN in .env (see .env.example)."
                )
            self.client = LinkedInClient(
                access_token=access_token,
                api_version=api_version or "202610",
                timeout=timeout,
            )
        self.transformer = LinkedInTransformer()
        self.api_version = self.client.api_version

    # -- connection ---------------------------------------------------------
    def test_connection(self) -> dict[str, Any]:
        """Verify the token and the Community Management product.

        Returns the member profile plus the count of administered Pages.
        A 401 means the token is expired/revoked; a 403 on the ACL call
        means the app lacks the Community Management product.
        """
        member = self.client.get_me()
        pages = self.get_administered_pages()
        logger.info(
            "LinkedIn connection successful: member %s, %d administered page(s)",
            member.get("id", "?"),
            len(pages),
        )
        return {"member": member, "administered_pages": len(pages), "pages": pages}

    def get_user(self) -> dict[str, Any]:
        """Return the authenticated member's profile (GET /rest/me)."""
        return self.client.get_me()

    # -- organisations ------------------------------------------------------
    def get_administered_pages(
        self, roles: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Pages the member administers, via organizationAcls.

        Filters to admin roles (ADMINISTRATOR, CONTENT_ADMIN,
        DIRECT_SPONSORED_CONTENT_POSTER) unless ``roles`` is given.
        Each entry: ``organization_urn``, ``organization_id``, ``role``,
        ``state``.
        """
        roles = roles or ADMIN_ROLES
        elements = self.client.list_organization_acls()
        pages = []
        for element in elements:
            role = str(element.get("role", ""))
            if role not in roles:
                continue
            urn = str(element.get("organization", ""))
            org_id = urn.split(":")[-1] if urn else ""
            pages.append(
                {
                    "organization_urn": urn,
                    "organization_id": org_id,
                    "role": role,
                    "state": str(element.get("state", "")),
                }
            )
        logger.info("Found %d administered LinkedIn page(s)", len(pages))
        return pages

    def get_organization(self, organization_id: str | int) -> dict[str, Any]:
        """Return Page details for an organisation id or URN."""
        return self.client.get_organization(organization_id)

    @staticmethod
    def organization_urn(organization_id: str | int) -> str:
        """Normalise an id or URN to ``urn:li:organization:<id>``."""
        text = str(organization_id)
        if text.startswith("urn:li:organization:"):
            return text
        return f"urn:li:organization:{text}"

    # -- posts ----------------------------------------------------------------
    def list_posts(
        self,
        organization_urn: str,
        count: int = 10,
        start: int = 0,
    ) -> dict[str, Any]:
        """One page of a Page's posts (newest first)."""
        return self.client.list_posts(
            author_urn=organization_urn, count=count, start=start
        )

    def list_all_posts(
        self,
        organization_urn: str,
        max_posts: int | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """All of a Page's posts, paginating until exhausted or ``max_posts``."""
        posts: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self.client.list_posts(
                author_urn=organization_urn, count=page_size, start=start
            )
            elements = page.get("elements", [])
            if not elements:
                break
            posts.extend(elements)
            logger.debug(
                "LinkedIn posts page: %d records (total so far %d)",
                len(elements),
                len(posts),
            )
            total = (page.get("paging") or {}).get("total")
            if max_posts is not None and len(posts) >= max_posts:
                posts = posts[:max_posts]
                break
            if total is not None:
                # Trust the server's total: a short page is not terminal
                # when more rows are declared.
                if len(posts) >= total:
                    break
            elif len(elements) < page_size:
                # No total declared: a short page means the last page.
                break
            start += len(elements)
        logger.info(
            "Retrieved %d post(s) for %s", len(posts), organization_urn
        )
        return posts

    def get_post(self, post_urn: str) -> dict[str, Any]:
        """Return a single post by URN."""
        return self.client.get_post(post_urn)

    def get_post_statistics(self, organization_urn: str) -> dict[str, Any]:
        """Page-level share statistics (last 30 days by default)."""
        return self.client.get_organization_share_statistics(organization_urn)

    def get_post_engagement(self, post_urn: str) -> dict[str, Any]:
        """Per-post like/comment summary (socialActions)."""
        try:
            return self.client.get_post_social_actions(post_urn)
        except LinkedInAPIError as exc:
            logger.debug(
                "No social actions for %s (%s); continuing without engagement",
                post_urn,
                exc,
            )
            return {}

    # -- DataFrame convenience --------------------------------------------------
    def posts_to_dataframe(
        self,
        posts: list[dict[str, Any]],
        organization: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """Transform raw post payloads into the canonical DataFrame."""
        return self.transformer.transform_posts(posts, organization)

    def organization_posts_to_dataframe(
        self,
        organization_urn: str,
        max_posts: int | None = None,
    ) -> pd.DataFrame:
        """Fetch a Page's posts and return the canonical DataFrame."""
        organization: dict[str, Any] | None = None
        try:
            organization = self.get_organization(organization_urn)
        except LinkedInAPIError as exc:
            logger.warning(
                "Could not load details for %s (%s); continuing with URN only",
                organization_urn,
                exc,
            )
        posts = self.list_all_posts(organization_urn, max_posts=max_posts)
        return self.posts_to_dataframe(posts, organization)
