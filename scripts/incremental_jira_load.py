"""Scheduled incremental entry point for the Jira ingestion pipeline.

This is a thin wrapper around :class:`src.pipeline.jira_pipeline.JiraPipeline`
intended for schedulers (cron, Airflow, Windows Task Scheduler, ...).
It always runs an incremental load: only issues updated since the last
successful run's watermark (minus the configured lookback window) are
extracted, so a nightly or hourly schedule stays cheap and idempotent.

Usage (from the project root)::

    python scripts/incremental_jira_load.py
    python scripts/incremental_jira_load.py --config config/config.yaml

Exit codes: 0 success, 1 configuration / authentication failure
(clean message, no traceback), 2 any other pipeline failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/incremental_jira_load.py` from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The pipeline import lives inside main() so `--help` works even before
# the pipeline's writer dependencies are in place.
from src.utils.config_loader import load_config  # noqa: E402
from src.utils.exceptions import ConfigError, JiraAuthError  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the incremental loader."""
    parser = argparse.ArgumentParser(
        description="Run an incremental Jira load (watermark-based).",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "config.yaml"),
        help="Path to the YAML configuration file.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override the log level from the configuration.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the incremental load. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        # Config first, so a config error exits 1 even if the pipeline's
        # own dependencies are unavailable.
        config = load_config(args.config)
        if args.log_level:
            logging_cfg = config.setdefault("logging", {})
            logging_cfg["level"] = args.log_level
            logging_cfg["console_level"] = args.log_level

        from src.pipeline.jira_pipeline import JiraPipeline

        pipeline = JiraPipeline(config=config, config_path=args.config)
        metadata = pipeline.run_incremental_load()
    except (ConfigError, JiraAuthError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI must map failures to exit 2
        print(f"ERROR: Pipeline failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"Incremental load {metadata.status}: run {metadata.pipeline_run_id}, "
        f"{metadata.records_extracted} extracted, "
        f"{metadata.records_inserted} inserted, "
        f"{metadata.records_updated} updated."
    )
    return 0 if metadata.status == "SUCCESS" else 2


if __name__ == "__main__":
    sys.exit(main())
