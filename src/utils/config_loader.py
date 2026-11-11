"""Configuration loading: YAML files with ``${ENV_VAR}`` expansion.

Supports ``${VAR}`` and ``${VAR:-default}`` placeholders anywhere in a
YAML document, resolved from the process environment (after loading a
``.env`` file with python-dotenv when one is present).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.utils.exceptions import ConfigError

_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} / ${VAR:-default} in strings."""
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ConfigError(
                f"Environment variable '{name}' is not set and no "
                "default was provided in the configuration."
            )

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def load_config(
    config_path: str | Path,
    env_path: str | Path | None = None,
    *,
    load_dotenv_file: bool = True,
) -> dict[str, Any]:
    """Load a YAML config file with environment-variable expansion.

    Args:
        config_path: Path to the YAML configuration file.
        env_path: Optional explicit ``.env`` file to load first.
        load_dotenv_file: When True, also load a ``.env`` file found next
            to the config file or in the current working directory.

    Returns:
        The configuration as a plain dictionary.

    Raises:
        ConfigError: If the file is missing, is not valid YAML, or
            references an unset environment variable without a default.
    """
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    if load_dotenv_file:
        if env_path is not None:
            load_dotenv(env_path, override=False)
        else:
            # Look next to the config, then in the project root / CWD.
            for candidate in (path.parent / ".env", Path(".env")):
                if candidate.exists():
                    load_dotenv(candidate, override=False)
                    break

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Configuration in {path} must be a YAML mapping.")
    return _expand_env(raw)


def load_fields_config(config_path: str | Path) -> dict[str, Any]:
    """Load the custom-field mapping file (``jira_fields.yaml``)."""
    path = Path(config_path)
    if not path.exists():
        return {"custom_fields": {}}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    return raw if isinstance(raw, dict) else {"custom_fields": {}}


def get_nested(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely read a nested mapping value, e.g. get_nested(cfg, 'jira', 'jql')."""
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def require_env(name: str) -> str:
    """Return an environment variable or raise ConfigError if unset/empty."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Required environment variable '{name}' is not set. "
            "See .env.example for the values the framework needs."
        )
    return value
