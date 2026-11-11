"""Command-line entry point for the Jira data ingestion pipeline.

Usage (from the project root)::

    python scripts/extract_jira.py
    python scripts/extract_jira.py --mode incremental
    python scripts/extract_jira.py --jql "project = TECH ORDER BY updated DESC"
    python scripts/extract_jira.py --output both --log-level DEBUG

Exit codes: 0 success, 1 configuration / authentication failure
(clean message, no traceback), 2 any other pipeline failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/extract_jira.py` from the project root: the
# project root (parent of scripts/) goes on sys.path so `import src...`
# works regardless of the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Imports of the pipeline itself live inside main() so that `--help`
# works even before the pipeline's writer dependencies are in place.
from src.utils.config_loader import load_config, load_fields_config  # noqa: E402
from src.utils.exceptions import ConfigError, JiraAuthError  # noqa: E402

_OUTPUT_MAP: dict[str, list[str]] = {
    "csv": ["csv"],
    "google_sheets": ["google_sheets"],
    "excel": ["excel"],
    "both": ["csv", "google_sheets"],
    "all": ["csv", "google_sheets", "excel"],
}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the extraction CLI."""
    parser = argparse.ArgumentParser(
        description="Extract Jira issues into CSV and/or Google Sheets.",
    )
    parser.add_argument(
        "--jql",
        default=None,
        help="JQL query to run (overrides the configured JQL).",
    )
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="full",
        help="Load mode: full (default) or incremental (watermark-based).",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "google_sheets", "excel", "both", "all"],
        default="csv",
        help="Output destination(s): csv (default), google_sheets, excel, "
        "both (csv + google_sheets), or all (csv + google_sheets + excel).",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "config.yaml"),
        help="Path to the YAML configuration file.",
    )
    parser.add_argument(
        "--fields-config",
        default=str(PROJECT_ROOT / "config" / "jira_fields.yaml"),
        help="Path to the custom-field mapping file.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override the log level from the configuration.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline from CLI arguments. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        # Config first, so a config error exits 1 even if the pipeline's
        # own dependencies are unavailable.
        config = load_config(args.config)
        fields_config = load_fields_config(args.fields_config)
        if args.log_level:
            logging_cfg = config.setdefault("logging", {})
            logging_cfg["level"] = args.log_level
            logging_cfg["console_level"] = args.log_level

        from src.pipeline.jira_pipeline import JiraPipeline
        from src.transformers.jira_transformer import JiraTransformer

        custom_fields = {
            field_id: str(spec.get("name", field_id))
            for field_id, spec in (fields_config.get("custom_fields") or {}).items()
            if isinstance(spec, dict)
        }
        transformer = JiraTransformer(
            base_url=str(config.get("jira", {}).get("base_url", "")),
            custom_fields=custom_fields,
            retain_unknown_custom_fields=bool(
                fields_config.get("retain_unknown_custom_fields", False)
            ),
        )
        pipeline = JiraPipeline(
            config=config,
            config_path=args.config,
            transformer=transformer,
        )
        metadata = pipeline.run(
            mode=args.mode,
            jql=args.jql,
            outputs=_OUTPUT_MAP[args.output],
        )
    except (ConfigError, JiraAuthError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI must map failures to exit 2
        print(f"ERROR: Pipeline failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"Pipeline {metadata.status}: run {metadata.pipeline_run_id}, "
        f"{metadata.records_extracted} extracted, "
        f"{metadata.records_inserted} inserted, "
        f"{metadata.records_updated} updated."
    )
    return 0 if metadata.status == "SUCCESS" else 2


if __name__ == "__main__":
    sys.exit(main())
