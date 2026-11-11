"""Centralised logging for the Jira ingestion framework (spec §32–§38).

Features
--------
* DEBUG / INFO / WARNING / ERROR / CRITICAL, with "normal" operations
  mapped to standard INFO.
* Console output plus rotating file handlers: an application log, an
  error-only log and a debug log (all configurable, §35).
* Independent console and file log levels.
* Text (default) or JSON structured output (§37).
* A unique ``pipeline_run_id`` injected into every log record via a
  context variable, so one execution can be traced across components.
* A redaction filter that masks API tokens, passwords, authorisation
  headers and other secrets before they can reach any log (§50).
* No silent exception swallowing: ``logger.exception`` always records
  the stack trace (§48).

Usage::

    logger = PipelineLogger.from_config(config["logging"], run_id=run_id)
    logger.info("Pipeline started", extra={"pipeline_name": "jira_issue_ingestion"})
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Run-id context
# ---------------------------------------------------------------------------
_current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jira_pipeline_run_id", default="-"
)


def set_run_id(run_id: str) -> None:
    """Set the run id attached to every log record in this context."""
    _current_run_id.set(run_id)


def get_run_id() -> str:
    """Return the run id for the current context (``"-"`` if unset)."""
    return _current_run_id.get()


@contextmanager
def run_id_context(run_id: str) -> Iterator[None]:
    """Temporarily bind a run id for the enclosed block."""
    token = _current_run_id.set(run_id)
    try:
        yield
    finally:
        _current_run_id.reset(token)


# ---------------------------------------------------------------------------
# Secret redaction (§50)
# ---------------------------------------------------------------------------
_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer <token> header form — MUST run first, or the
    # generic key=value pattern below would consume "Bearer" as the value
    # and leave the token itself in the log.
    (re.compile(r"(?i)(Authorization:\s*Bearer\s+)\S+"), r"\1********"),
    # key=value or key: value pairs, e.g. JIRA_API_TOKEN=abc123.
    # The negative lookahead keeps an already-redacted "Bearer ********"
    # pair from being mangled a second time.
    (
        re.compile(
            r"(?i)(api[_-]?token|password|passwd|secret|client[_-]?secret"
            r"|access[_-]?token|refresh[_-]?token|authorization|bearer)"
            r"(\s*[:=]\s*(?!Bearer\b)['\"]?)[^\s'\",}]+"
        ),
        r"\1\2********",
    ),
    # JSON-style "private_key": "-----BEGIN..."
    (re.compile(r'(?i)("private_key"\s*:\s*")[^"]+'), r"\1********"),
    # Basic-auth style user:password@host in a URL
    (re.compile(r"(?i)(://[^:/\s]+:)[^@\s]+(@)"), r"\1********\2"),
]


def redact_text(text: str) -> str:
    """Mask secrets in a log string. Never raises."""
    try:
        for pattern, replacement in _REDACTION_PATTERNS:
            text = pattern.sub(replacement, text)
    except Exception:  # noqa: BLE001 — redaction must never break logging
        return "[redaction failed]"
    return text


class RedactionFilter(logging.Filter):
    """Filter that redacts secrets from a log record's message and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, dict):
            record.args = {
                key: redact_text(str(value)) for key, value in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(
                redact_text(str(arg)) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


class RunIdFilter(logging.Filter):
    """Attach the current ``pipeline_run_id`` to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "pipeline_run_id"):
            record.pipeline_run_id = get_run_id()
        return True


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------
_TEXT_FORMAT = (
    "%(asctime)s %(levelname)-8s [%(pipeline_run_id)s] "
    "%(name)s.%(funcName)s - %(message)s"
)


class JsonFormatter(logging.Formatter):
    """Structured JSON log lines (§37)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "pipeline_name": getattr(record, "pipeline_name", "jira_issue_ingestion"),
            "pipeline_run_id": getattr(record, "pipeline_run_id", "-"),
            "module": record.module,
            "function": record.funcName,
            "message": record.getMessage(),
        }
        for extra_key in ("duration", "record_count", "stage", "records"):
            if hasattr(record, extra_key):
                payload[extra_key] = getattr(record, extra_key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _build_formatter(fmt: str) -> logging.Formatter:
    return JsonFormatter() if fmt.lower() == "json" else logging.Formatter(_TEXT_FORMAT)


# ---------------------------------------------------------------------------
# PipelineLogger
# ---------------------------------------------------------------------------
DEFAULT_FILES = {
    "application": "jira_pipeline.log",
    "error": "jira_pipeline_error.log",
    "debug": "jira_pipeline_debug.log",
}


class PipelineLogger:
    """Logger configured from the ``logging:`` section of config.yaml.

    Creates a console handler plus rotating file handlers for the
    application log, an error-only log and a debug log. All handlers
    carry the redaction and run-id filters.
    """

    def __init__(
        self,
        name: str = "jira_pipeline",
        level: str = "INFO",
        console_level: str | None = None,
        log_dir: str | Path = "logs",
        files: dict[str, str] | None = None,
        fmt: str = "text",
        rotation: dict[str, Any] | None = None,
        enabled: bool = True,
        run_id: str | None = None,
    ) -> None:
        self.name = name
        self.enabled = enabled
        if run_id:
            set_run_id(run_id)
        self.logger = logging.getLogger(name)
        # The logger itself accepts everything; the handlers filter by
        # level. Setting the logger to the configured level would drop
        # DEBUG records before the debug file handler ever saw them.
        self.logger.setLevel(logging.DEBUG)
        # Keep propagation on: the root logger has no handlers in normal
        # use (so nothing is printed twice), and it lets test tooling
        # such as pytest's caplog observe records from child loggers.
        self.logger.propagate = True
        # Rebuild handlers each time so tests can reconfigure cleanly.
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()

        if not enabled:
            self.logger.addHandler(logging.NullHandler())
            return

        formatter = _build_formatter(fmt)
        filters = [RunIdFilter(), RedactionFilter()]

        console = logging.StreamHandler()
        console.setLevel(
            getattr(logging, (console_level or level).upper(), logging.INFO)
        )
        console.setFormatter(formatter)
        for f in filters:
            console.addFilter(f)
        self.logger.addHandler(console)

        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        rotation = rotation or {}
        max_bytes = int(rotation.get("max_bytes", 10 * 1024 * 1024))
        backup_count = int(rotation.get("backup_count", 10))
        rotation_enabled = bool(rotation.get("enabled", True))
        file_level = getattr(logging, level.upper(), logging.INFO)
        resolved_files = {**DEFAULT_FILES, **(files or {})}

        app_handler = self._rotating_handler(
            log_path / resolved_files["application"],
            file_level,
            max_bytes,
            backup_count,
            rotation_enabled,
        )
        app_handler.setFormatter(formatter)
        for f in filters:
            app_handler.addFilter(f)
        self.logger.addHandler(app_handler)

        error_handler = self._rotating_handler(
            log_path / resolved_files["error"],
            logging.ERROR,
            max_bytes,
            backup_count,
            rotation_enabled,
        )
        error_handler.setFormatter(formatter)
        for f in filters:
            error_handler.addFilter(f)
        self.logger.addHandler(error_handler)

        debug_handler = self._rotating_handler(
            log_path / resolved_files["debug"],
            logging.DEBUG,
            max_bytes,
            backup_count,
            rotation_enabled,
        )
        debug_handler.setFormatter(formatter)
        for f in filters:
            debug_handler.addFilter(f)
        self.logger.addHandler(debug_handler)

    @staticmethod
    def _rotating_handler(
        path: Path,
        level: int,
        max_bytes: int,
        backup_count: int,
        rotation_enabled: bool,
    ) -> logging.Handler:
        if rotation_enabled:
            handler: logging.Handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
        else:
            handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(level)
        return handler

    @classmethod
    def from_config(
        cls,
        logging_config: dict[str, Any] | None,
        run_id: str | None = None,
    ) -> "PipelineLogger":
        """Build a logger from the ``logging:`` config mapping (§35)."""
        cfg = logging_config or {}
        return cls(
            level=str(cfg.get("level", "INFO")),
            console_level=(
                str(cfg["console_level"])
                if cfg.get("console_level") is not None
                else None
            ),
            log_dir=str(cfg.get("directory", "logs")),
            files=cfg.get("files"),
            fmt=str(cfg.get("format", "text")),
            rotation=cfg.get("rotation"),
            enabled=bool(cfg.get("enabled", True)),
            run_id=run_id,
        )

    # -- level helpers ------------------------------------------------------
    # Signatures mirror the standard library: message, *args for %-style
    # formatting, plus **extra for structured fields (record_count etc.).
    # Calls go straight to the underlying logger so the recorded
    # function name is the level method, not a private helper.
    def debug(self, message: str, *args: Any, **extra: Any) -> None:
        self.logger.debug(message, *args, extra=extra)

    def info(self, message: str, *args: Any, **extra: Any) -> None:
        self.logger.info(message, *args, extra=extra)

    def warning(self, message: str, *args: Any, **extra: Any) -> None:
        self.logger.warning(message, *args, extra=extra)

    def error(self, message: str, *args: Any, **extra: Any) -> None:
        self.logger.error(message, *args, extra=extra)

    def critical(self, message: str, *args: Any, **extra: Any) -> None:
        self.logger.critical(message, *args, extra=extra)

    def exception(self, message: str, *args: Any, **extra: Any) -> None:
        """Log an error with the full stack trace (§48)."""
        self.logger.exception(message, *args, extra=extra)

    def child(self, name: str) -> "PipelineLogger":
        """Return a logger sharing this configuration under a child name."""
        child = object.__new__(PipelineLogger)
        child.name = f"{self.name}.{name}"
        child.enabled = self.enabled
        child.logger = logging.getLogger(child.name)
        child.logger.setLevel(self.logger.level)
        child.logger.propagate = True  # parent handlers emit the record
        return child

    def close(self) -> None:
        """Flush and close all handlers."""
        for handler in list(self.logger.handlers):
            handler.flush()
            handler.close()
            self.logger.removeHandler(handler)


def get_logger(name: str = "jira_pipeline") -> logging.Logger:
    """Return a standard logger that still carries run-id/redaction filters."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, RunIdFilter) for h in logger.handlers for f in h.filters):
        logger.addFilter(RunIdFilter())
        logger.addFilter(RedactionFilter())
    return logger
