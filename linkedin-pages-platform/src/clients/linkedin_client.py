"""Low-level LinkedIn REST API client (official Marketing Developer Platform).

A thin wrapper around :mod:`requests` that owns authentication, retries
and error mapping — nothing else. It knows nothing about DataFrames, CSV
or Google Sheets.

API surface
-----------
* Base URL ``https://api.linkedin.com/rest`` with the required headers
  ``LinkedIn-Version: <version>`` and
  ``X-Restli-Protocol-Version: 2.0.0`` on every call. LinkedIn versions
  its Marketing APIs monthly and sunsets old versions, so the version
  is configurable (``LINKEDIN_API_VERSION`` / ``config.yaml``).
* OAuth 2.0 bearer tokens. Token acquisition/refresh lives in
  ``scripts/linkedin_auth.py``; this client only sends the token and
  reports a 401 so the caller can refresh and retry once.
* Read-only endpoints used by the pipeline: ``/me``,
  ``/organizationAcls``, ``/organizations/{id}``, ``/posts`` (list by
  author), ``/posts/{id}``, ``/socialActions/{urn}`` (per-post
  engagement) and ``/organizationalEntityShareStatistics``.

Retry policy
------------
* Retried with exponential backoff: 429 (rate limit), 500, 502, 503,
  network timeouts and connection failures.
* Never retried: 401 / 403 (bad token, missing scope or missing
  Community Management product) and 400 (bad request).
"""

from __future__ import annotations

from typing import Any

import requests
import tenacity

from src.utils.exceptions import (
    LinkedInAPIError,
    LinkedInAuthError,
    LinkedInConnectionError,
    LinkedInForbiddenError,
    LinkedInNotFoundError,
    LinkedInRateLimitError,
    LinkedInServerError,
)
from src.utils.logger import get_logger

logger = get_logger("linkedin_pipeline.linkedin_client")

DEFAULT_API_VERSION = "202610"
REST_BASE_URL = "https://api.linkedin.com/rest"
OAUTH_BASE_URL = "https://www.linkedin.com/oauth/v2"

# Statuses worth retrying. 401/403/400/404 are permanent and never retried.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (LinkedInRateLimitError, LinkedInServerError, LinkedInConnectionError),
    )


class LinkedInClient:
    """HTTP client for LinkedIn's versioned REST API.

    Args:
        access_token: OAuth 2.0 bearer token for the authenticated member.
        api_version: LinkedIn API version, e.g. ``"202610"``. LinkedIn
            sunsets versions roughly a year after release; bump this
            when the configured version approaches sunset.
        timeout: Per-request timeout in seconds.
        max_retries: How many times a retryable failure is retried.
    """

    def __init__(
        self,
        access_token: str | None = None,
        api_version: str = DEFAULT_API_VERSION,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self.access_token = access_token
        self.api_version = api_version
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_count = 0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "LinkedIn-Version": api_version,
                "X-Restli-Protocol-Version": "2.0.0",
            }
        )
        if access_token:
            self.session.headers["Authorization"] = f"Bearer {access_token}"

    def set_access_token(self, access_token: str) -> None:
        """Replace the bearer token (e.g. after a refresh)."""
        self.access_token = access_token
        self.session.headers["Authorization"] = f"Bearer {access_token}"

    # -- internals ----------------------------------------------------------
    def _map_status(self, status: int, body: str) -> LinkedInAPIError:
        if status == 401:
            return LinkedInAuthError(
                "LinkedIn authentication failed (HTTP 401). The access "
                "token is missing, expired or revoked — refresh it with "
                "scripts/linkedin_auth.py --refresh.",
                status_code=status,
                response_body=body,
            )
        if status == 403:
            return LinkedInForbiddenError(
                "LinkedIn refused the request (HTTP 403). The token lacks "
                "the required scope, or the LinkedIn app has not been "
                "approved for the Community Management API product, or the "
                "member is not an admin of the requested Page.",
                status_code=status,
                response_body=body,
            )
        if status == 404:
            return LinkedInNotFoundError(
                f"LinkedIn resource not found (HTTP 404): {body[:500]}",
                status_code=status,
                response_body=body,
            )
        if status == 429:
            return LinkedInRateLimitError(
                "LinkedIn rate limit reached (HTTP 429).",
                status_code=status,
                response_body=body,
            )
        if status >= 500:
            return LinkedInServerError(
                f"LinkedIn server error (HTTP {status}).",
                status_code=status,
                response_body=body,
            )
        return LinkedInAPIError(
            f"LinkedIn API error (HTTP {status}): {body[:500]}",
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
        url = f"{REST_BASE_URL}{path}"

        def _attempt() -> Any:
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    timeout=self.timeout,
                )
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as exc:
                raise LinkedInConnectionError(
                    f"Could not reach LinkedIn at {REST_BASE_URL}: {exc}"
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise LinkedInConnectionError(
                    f"LinkedIn request failed: {exc}"
                ) from exc

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                logger.warning(
                    "LinkedIn rate limit (429)%s",
                    f"; Retry-After: {retry_after}s" if retry_after else "",
                )
            if response.status_code >= 400:
                raise self._map_status(response.status_code, response.text)
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise LinkedInAPIError(
                    f"Malformed JSON in LinkedIn response from {path}",
                    status_code=response.status_code,
                ) from exc

        retrying = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(self.max_retries + 1),
            wait=tenacity.wait_exponential(multiplier=1, min=1, max=30),
            retry=tenacity.retry_if_exception(_is_retryable),
            reraise=True,
            before_sleep=lambda state: logger.warning(
                "Retrying LinkedIn request (attempt %s/%s): %s",
                state.attempt_number,
                self.max_retries + 1,
                state.outcome.exception(),
            ),
        )
        try:
            result = retrying(_attempt)
        except (
            LinkedInRateLimitError,
            LinkedInServerError,
            LinkedInConnectionError,
        ):
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
        logger.debug("LinkedIn GET %s params=%s", path, params)
        result = self._request("GET", path, params=params)
        return result if isinstance(result, dict) else {"elements": result}

    def post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("LinkedIn POST %s", path)
        result = self._request("POST", path, payload=payload)
        return result if isinstance(result, dict) else {"elements": result}

    # -- member / organisation endpoints ------------------------------------
    def get_me(self) -> dict[str, Any]:
        """GET /rest/me — the authenticated member's profile."""
        return self.get("/me")

    def list_organization_acls(
        self,
        role: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /rest/organizationAcls?q=roleAssignee — Pages the member administers.

        Each element carries ``organization`` (``urn:li:organization:<id>``),
        ``role`` (e.g. ADMINISTRATOR) and ``state`` (e.g. APPROVED).
        """
        params: dict[str, Any] = {"q": "roleAssignee"}
        if role:
            params["role"] = role
        if state:
            params["state"] = state
        data = self.get("/organizationAcls", params=params)
        return data.get("elements", [])

    def get_organization(self, organization_id: str | int) -> dict[str, Any]:
        """GET /rest/organizations/{id} — Page details (name, vanity, logo...)."""
        org_id = str(organization_id).removeprefix("urn:li:organization:")
        return self.get(f"/organizations/{org_id}")

    # -- posts endpoints ------------------------------------------------------
    def list_posts(
        self,
        author_urn: str,
        count: int = 10,
        start: int = 0,
        sort_by: str = "CREATED",
    ) -> dict[str, Any]:
        """GET /rest/posts?q=author — one page of a Page's posts.

        Response carries ``elements`` (posts) and ``paging`` (``total``,
        ``count``, ``start``). Posts are returned newest-first when
        ``sort_by="CREATED"``.
        """
        params: dict[str, Any] = {
            "q": "author",
            "author": author_urn,
            "count": count,
            "start": start,
            "sortBy": sort_by,
        }
        return self.get("/posts", params=params)

    def get_post(self, post_urn: str) -> dict[str, Any]:
        """GET /rest/posts/{post-id} — a single post.

        ``post_urn`` may be a full URN (``urn:li:share:123``) or the bare
        id; the URN prefix is stripped for the path.
        """
        post_id = post_urn.split(":")[-1]
        return self.get(f"/posts/{post_id}")

    def get_post_social_actions(self, post_urn: str) -> dict[str, Any]:
        """GET /rest/socialActions/{urn} — per-post engagement summary.

        Returns like/comment summaries for the post. Optional enrichment:
        a 404 here means the post has no social actions yet.
        """
        return self.get(f"/socialActions/{post_urn}")

    def get_organization_share_statistics(
        self,
        organization_urn: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> dict[str, Any]:
        """GET /rest/organizationalEntityShareStatistics — Page-level totals.

        Aggregate share/like/comment/click/impression counts for the Page
        over the given epoch-millis window (defaults to the last 30 days
        when omitted). Requires the ``rw_organization_admin`` scope.
        """
        if end_ms is None:
            from src.utils.date_utils import now_utc

            end_ms = int(now_utc().timestamp() * 1000)
        if start_ms is None:
            start_ms = end_ms - 30 * 24 * 60 * 60 * 1000
        params: dict[str, Any] = {
            "q": "organizationalEntity",
            "organizationalEntity": organization_urn,
            "timeIntervals": (
                f"(timeRange:(start:{start_ms},end:{end_ms}),"
                "timeGranularityType:DAY)"
            ),
        }
        return self.get("/organizationalEntityShareStatistics", params=params)

    # -- OAuth token endpoints (used by scripts/linkedin_auth.py) ------------
    @staticmethod
    def exchange_code_for_tokens(
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """POST /oauth/v2/accessToken — authorization code -> tokens."""
        response = requests.post(
            f"{OAUTH_BASE_URL}/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise LinkedInAuthError(
                f"LinkedIn token exchange failed (HTTP "
                f"{response.status_code}): {response.text[:500]}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()

    @staticmethod
    def refresh_access_token(
        client_id: str,
        client_secret: str,
        refresh_token: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """POST /oauth/v2/accessToken — refresh token -> new access token.

        Note: LinkedIn issues programmatic refresh tokens only to
        eligible approved partners. Without one, re-authorize through
        the browser flow instead (``scripts/linkedin_auth.py``).
        """
        response = requests.post(
            f"{OAUTH_BASE_URL}/accessToken",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
        if response.status_code >= 400:
            raise LinkedInAuthError(
                f"LinkedIn token refresh failed (HTTP "
                f"{response.status_code}): {response.text[:500]}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()
