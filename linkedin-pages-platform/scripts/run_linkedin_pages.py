"""CLI: run the LinkedIn Company Pages ingestion pipeline.

Usage
-----
Discover the Pages you administer::

    python scripts/run_linkedin_pages.py --discover

Full run for every configured organisation::

    python scripts/run_linkedin_pages.py

Incremental run (only posts newer than the last successful watermark)::

    python scripts/run_linkedin_pages.py --mode incremental

One organisation, capped at 100 posts, debug logging::

    python scripts/run_linkedin_pages.py --org 12345678 --max-posts 100 --debug

Requirements: LINKEDIN_ACCESS_TOKEN in .env (see scripts/linkedin_auth.py),
and the LinkedIn app approved for the Community Management API product.
Nothing here scrapes LinkedIn — all data comes from the official API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the project root as ``python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.handlers.linkedin_handler import LinkedInPagesHandler  # noqa: E402
from src.pipeline.linkedin_pages_pipeline import (  # noqa: E402
    LinkedInPagesPipeline,
    run_summary,
)
from src.utils.config_loader import load_config  # noqa: E402
from src.utils.exceptions import ConfigError  # noqa: E402


def _load_merged_config(root: Path) -> dict:
    """Load config.yaml and merge linkedin_pages.yaml organisations in."""
    config = load_config(root / "config" / "config.yaml")
    pages_file = root / "config" / "linkedin_pages.yaml"
    if pages_file.exists():
        pages_config = load_config(pages_file, load_dotenv_file=False)
        organisations = pages_config.get("organizations") or []
        config.setdefault("linkedin", {})
        existing = config["linkedin"].get("organizations") or []
        config["linkedin"]["organizations"] = existing + organisations
    return config


def _discover(config: dict) -> int:
    """Print every Page the member administers, then exit."""
    from src.utils.config_loader import require_env

    token = require_env("LINKEDIN_ACCESS_TOKEN")
    handler = LinkedInPagesHandler(
        access_token=token,
        api_version=str(
            config.get("linkedin", {}).get("api_version") or "202610"
        ),
    )
    pages = handler.get_administered_pages()
    if not pages:
        print("No administered LinkedIn Company Pages found for this member.")
        return 0
    print(f"{len(pages)} administered page(s):")
    for page in pages:
        print(
            f"  - {page['organization_urn']} "
            f"(role={page['role']}, state={page['state']})"
        )
    print("\nCopy the organisation ids into config/linkedin_pages.yaml.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest LinkedIn Company Page posts via the official API."
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="full",
        help="full: all posts; incremental: only posts newer than the watermark.",
    )
    parser.add_argument(
        "--org",
        action="append",
        dest="orgs",
        default=None,
        help="Organisation id or URN to ingest (repeatable). Overrides config.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=None,
        help="Max posts per organisation (default: config max_posts).",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "google_sheets", "both", "all"],
        default="csv",
        help="Output destination (default: csv). google_sheets needs config.",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="List administered Pages and exit.",
    )
    parser.add_argument("--debug", action="store_true", help="DEBUG log level.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    try:
        config = _load_merged_config(root)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.debug:
        config.setdefault("logging", {})["level"] = "DEBUG"
        config["logging"]["console_level"] = "DEBUG"
    if args.output in ("google_sheets", "both", "all"):
        config.setdefault("google_sheets", {})["enabled"] = True

    try:
        if args.discover:
            return _discover(config)
        pipeline = LinkedInPagesPipeline(config)
        metadata = pipeline.run(
            mode=args.mode,
            organizations=args.orgs,
            max_posts=args.max_posts,
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — CLI reports and exits non-zero
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(run_summary(metadata))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
