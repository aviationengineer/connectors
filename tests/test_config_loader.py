"""Tests for YAML config loading and ${VAR} expansion."""

from __future__ import annotations

import pytest

from src.utils.config_loader import (
    get_nested,
    load_config,
    load_fields_config,
    require_env,
)
from src.utils.exceptions import ConfigError


def _write(path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


def test_env_var_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://acme.atlassian.net")
    path = _write(
        tmp_path / "config.yaml",
        "jira:\n  base_url: ${JIRA_BASE_URL}\n  timeout: 30\n",
    )
    config = load_config(path, load_dotenv_file=False)
    assert config["jira"]["base_url"] == "https://acme.atlassian.net"
    assert config["jira"]["timeout"] == 30


def test_env_var_default_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_AUTH_TYPE", raising=False)
    path = _write(
        tmp_path / "config.yaml",
        "jira:\n  auth_type: ${JIRA_AUTH_TYPE:-basic}\n",
    )
    config = load_config(path, load_dotenv_file=False)
    assert config["jira"]["auth_type"] == "basic"


def test_env_var_in_middle_of_string(tmp_path, monkeypatch):
    monkeypatch.setenv("JIRA_HOST", "acme")
    path = _write(
        tmp_path / "config.yaml",
        "jira:\n  base_url: https://${JIRA_HOST}.atlassian.net\n",
    )
    config = load_config(path, load_dotenv_file=False)
    assert config["jira"]["base_url"] == "https://acme.atlassian.net"


def test_missing_var_without_default_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    path = _write(
        tmp_path / "config.yaml",
        "jira:\n  base_url: ${JIRA_BASE_URL}\n",
    )
    with pytest.raises(ConfigError, match="JIRA_BASE_URL"):
        load_config(path, load_dotenv_file=False)


def test_get_nested_reads_nested_values_and_defaults():
    config = {"jira": {"jql": {"issues": "project = DATA"}}, "a": {"b": {"c": 5}}}
    assert get_nested(config, "jira", "jql", "issues") == "project = DATA"
    assert get_nested(config, "a", "b", "c") == 5
    assert get_nested(config, "a", "missing", default="fallback") == "fallback"
    assert get_nested(config, "missing") is None


def test_invalid_yaml_raises_config_error(tmp_path):
    path = _write(tmp_path / "bad.yaml", "jira: [unclosed\n  bad indent: :\n")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(path, load_dotenv_file=False)


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does-not-exist.yaml", load_dotenv_file=False)


def test_non_mapping_yaml_raises_config_error(tmp_path):
    path = _write(tmp_path / "list.yaml", "- one\n- two\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(path, load_dotenv_file=False)


def test_load_fields_config_missing_file_returns_empty_mapping(tmp_path):
    assert load_fields_config(tmp_path / "nope.yaml") == {"custom_fields": {}}


def test_load_fields_config_reads_mapping(tmp_path):
    path = _write(
        tmp_path / "jira_fields.yaml",
        "custom_fields:\n  customfield_10001:\n    name: story_points\n"
        "retain_unknown_custom_fields: false\n",
    )
    fields = load_fields_config(path)
    assert fields["custom_fields"]["customfield_10001"]["name"] == "story_points"
    assert fields["retain_unknown_custom_fields"] is False


def test_require_env_returns_value_or_raises(monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "token-123")
    assert require_env("JIRA_API_TOKEN") == "token-123"
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="JIRA_API_TOKEN"):
        require_env("JIRA_API_TOKEN")
