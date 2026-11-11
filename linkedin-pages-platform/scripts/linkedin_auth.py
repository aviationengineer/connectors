"""LinkedIn OAuth 2.0 helper — obtain and refresh API tokens.

LinkedIn's official API uses three-legged OAuth: the member authorises
the developer app in the browser, LinkedIn redirects with a code, and
this script exchanges the code for tokens.

Usage
-----
1. Create a LinkedIn developer app and request the **Community
   Management API** product with scopes:
   ``r_organization_social``, ``rw_organization_admin``,
   ``r_organization_social_feed``.
2. Set LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET / LINKEDIN_REDIRECT_URI
   in ``.env`` (see .env.example).
3. Run::

       python scripts/linkedin_auth.py

   Open the printed URL, authorise, and paste the ``code`` from the
   redirect back here. Tokens are written to ``.env`` (mode 0600,
   git-ignored) — never printed in full, never committed.

4. Refresh later (only works if LinkedIn issued a refresh token —
   programmatic refresh tokens go to eligible approved partners; without
   one, re-run the browser flow)::

       python scripts/linkedin_auth.py --refresh

Security: client secrets and tokens are read from the environment / .env
only. Nothing is written to the repo except ``.env`` (git-ignored).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

# Allow running from the project root as ``python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from src.clients.linkedin_client import LinkedInClient  # noqa: E402
from src.utils.exceptions import ConfigError  # noqa: E402

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
SCOPES = ["r_organization_social", "rw_organization_admin", "r_organization_social_feed"]


def _env_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def _write_env(updates: dict[str, str]) -> None:
    """Upsert keys in .env, creating it mode 0600 when missing."""
    path = _env_path()
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    keys = set(updates)
    kept = [
        line
        for line in lines
        if not line.strip() or line.split("=", 1)[0].strip() not in keys
    ]
    kept.extend(f"{key}={value}" for key, value in updates.items())
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _mask(token: str) -> str:
    return f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "********"


def build_authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def run_authorization_flow() -> int:
    load_dotenv(_env_path())
    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get(
        "LINKEDIN_REDIRECT_URI", "http://localhost:8080"
    ).strip()
    if not client_id or not client_secret:
        print(
            "LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set in .env.\n"
            "Create them in the LinkedIn Developer Portal (your app's Auth tab),",
            file=sys.stderr,
        )
        return 2
    state = secrets.token_urlsafe(16)
    print("Open this URL in your browser and authorise the app:\n")
    print(build_authorization_url(client_id, redirect_uri, state))
    print(
        "\nLinkedIn will redirect to your redirect URI with a ?code=... "
        "parameter."
    )
    code = input("Paste the code here: ").strip()
    if not code:
        print("No code supplied; nothing was stored.", file=sys.stderr)
        return 2
    tokens = LinkedInClient.exchange_code_for_tokens(
        client_id, client_secret, code, redirect_uri
    )
    access = tokens.get("access_token", "")
    if not access:
        print(f"Token exchange returned no access token: {tokens}", file=sys.stderr)
        return 1
    updates = {"LINKEDIN_ACCESS_TOKEN": access}
    if tokens.get("refresh_token"):
        updates["LINKEDIN_REFRESH_TOKEN"] = tokens["refresh_token"]
    _write_env(updates)
    print(
        f"\nAccess token stored in .env ({_mask(access)}), "
        f"expires in {tokens.get('expires_in', '?')}s."
    )
    if tokens.get("refresh_token"):
        print("Refresh token stored too.")
    else:
        print(
            "No refresh token was issued (LinkedIn grants programmatic "
            "refresh tokens to eligible approved partners only). Re-run "
            "this flow when the access token expires."
        )
    return 0


def run_refresh_flow() -> int:
    load_dotenv(_env_path())
    client_id = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("LINKEDIN_REFRESH_TOKEN", "").strip()
    if not client_id or not client_secret or not refresh_token:
        print(
            "LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET and "
            "LINKEDIN_REFRESH_TOKEN must all be set in .env to refresh.\n"
            "Without a refresh token, re-run the browser flow instead.",
            file=sys.stderr,
        )
        return 2
    tokens = LinkedInClient.refresh_access_token(
        client_id, client_secret, refresh_token
    )
    access = tokens.get("access_token", "")
    if not access:
        print(f"Token refresh returned no access token: {tokens}", file=sys.stderr)
        return 1
    updates = {"LINKEDIN_ACCESS_TOKEN": access}
    if tokens.get("refresh_token"):
        updates["LINKEDIN_REFRESH_TOKEN"] = tokens["refresh_token"]
    _write_env(updates)
    print(f"Access token refreshed and stored in .env ({_mask(access)}).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LinkedIn OAuth helper: authorise or refresh API tokens."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh the access token instead of the browser flow.",
    )
    args = parser.parse_args()
    try:
        return run_refresh_flow() if args.refresh else run_authorization_flow()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
