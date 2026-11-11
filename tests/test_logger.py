"""Tests for the pipeline logger (levels, redaction, JSON, rotation)."""

from __future__ import annotations

import json
import logging.handlers

import pytest

from src.utils.logger import PipelineLogger, get_run_id, run_id_context, set_run_id


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def logger(log_dir):
    logger = PipelineLogger.from_config(
        {
            "level": "INFO",
            "console_level": "INFO",
            "directory": str(log_dir),
            "format": "text",
            "rotation": {"enabled": True, "max_bytes": 10485760, "backup_count": 10},
        },
        run_id="test-run-001",
    )
    yield logger
    logger.close()


def _read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_info_message_in_application_log_and_console(tmp_path, capfd):
    # Build the logger inside the test body so its console handler binds
    # the test phase's sys.stderr (a fixture-built handler binds the
    # setup phase's stream, which capfd cannot see).
    log_dir = tmp_path / "logs"
    logger = PipelineLogger.from_config(
        {
            "level": "INFO",
            "console_level": "INFO",
            "directory": str(log_dir),
            "format": "text",
        },
        run_id="test-run-001",
    )
    try:
        logger.info("Pipeline started")
    finally:
        logger.close()
    assert "Pipeline started" in _read(log_dir / "jira_pipeline.log")
    captured = capfd.readouterr()
    assert "Pipeline started" in captured.err


def test_debug_suppressed_at_info_but_present_in_debug_log(logger, log_dir):
    logger.debug("A debug detail")
    logger.info("An info line")
    logger.close()
    assert "A debug detail" not in _read(log_dir / "jira_pipeline.log")
    assert "A debug detail" in _read(log_dir / "jira_pipeline_debug.log")
    assert "An info line" in _read(log_dir / "jira_pipeline.log")


def test_error_lands_in_error_log(logger, log_dir):
    logger.error("Something failed badly")
    logger.close()
    assert "Something failed badly" in _read(log_dir / "jira_pipeline_error.log")
    assert "Something failed badly" in _read(log_dir / "jira_pipeline.log")


def test_secrets_are_redacted_from_log_files(logger, log_dir):
    logger.info("Using JIRA_API_TOKEN=secret-value-123 for auth")
    logger.info("Authorization: Bearer abc123")
    logger.close()
    for name in ("jira_pipeline.log", "jira_pipeline_debug.log"):
        content = _read(log_dir / name)
        assert "secret-value-123" not in content
        assert "abc123" not in content
        assert "********" in content


def test_json_format_produces_parseable_lines_with_run_id(log_dir):
    logger = PipelineLogger.from_config(
        {
            "level": "INFO",
            "console_level": "INFO",
            "directory": str(log_dir),
            "format": "json",
        },
        run_id="json-run-42",
    )
    try:
        logger.info("JSON log line")
    finally:
        logger.close()
    lines = [
        line for line in _read(log_dir / "jira_pipeline.log").splitlines() if line.strip()
    ]
    assert lines, "expected at least one JSON log line"
    record = json.loads(lines[0])
    assert record["message"] == "JSON log line"
    assert record["level"] == "INFO"
    assert record["pipeline_run_id"] == "json-run-42"


def test_run_id_context_sets_id_in_records(log_dir):
    logger = PipelineLogger.from_config(
        {"level": "INFO", "directory": str(log_dir), "format": "text"},
        run_id="outer-run",
    )
    try:
        with run_id_context("inner-run-7"):
            assert get_run_id() == "inner-run-7"
            logger.info("Inside the context")
        assert get_run_id() == "outer-run"
        logger.info("Outside the context")
    finally:
        logger.close()
    content = _read(log_dir / "jira_pipeline.log")
    assert "inner-run-7" in content
    assert "outer-run" in content


def test_rotation_uses_rotating_handler_and_rolls_over(log_dir):
    logger = PipelineLogger.from_config(
        {
            "level": "DEBUG",
            "directory": str(log_dir),
            "format": "text",
            "rotation": {"enabled": True, "max_bytes": 200, "backup_count": 2},
        },
        run_id="rotation-run",
    )
    app_handler = next(
        h for h in logger.logger.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert isinstance(app_handler, logging.handlers.RotatingFileHandler)
    assert app_handler.maxBytes == 200
    assert app_handler.backupCount == 2
    for i in range(60):
        logger.info(f"Rotation test message number {i} with padding xxxxxxxxxx")
    logger.close()
    assert (log_dir / "jira_pipeline.log.1").exists()


def test_set_and_get_run_id():
    set_run_id("manual-run-9")
    assert get_run_id() == "manual-run-9"
