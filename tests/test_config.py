"""Тесты `config.Config.from_env` и парсера числовых env-переменных.

Принцип «no silent fallback»: битое значение → `ValidationError`, а не
тихая замена на default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_egrul.config import Config, _parse_float_env
from mcp_egrul.constants import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    ENV_DB_PATH,
    ENV_DUMPS_DIR,
    ENV_HOSTED_API_BASE,
    ENV_HOSTED_API_KEY,
    ENV_HTTP_TIMEOUT,
    ENV_LOG_LEVEL,
    ENV_USER_AGENT,
)
from mcp_egrul.errors import ValidationError


def test_config_from_env_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "x.sqlite"
    dumps = tmp_path / "d"
    monkeypatch.setenv(ENV_DB_PATH, str(db))
    monkeypatch.setenv(ENV_DUMPS_DIR, str(dumps))
    monkeypatch.setenv(ENV_USER_AGENT, "ua-x/1.0")
    monkeypatch.setenv(ENV_HTTP_TIMEOUT, "42.5")
    monkeypatch.setenv(ENV_LOG_LEVEL, "debug")
    monkeypatch.setenv(ENV_HOSTED_API_KEY, "SECRET")
    monkeypatch.setenv(ENV_HOSTED_API_BASE, "https://example.test/v1")

    c = Config.from_env()

    assert c.db_path == db
    assert c.dumps_dir == dumps
    assert c.user_agent == "ua-x/1.0"
    assert c.http_timeout_seconds == 42.5
    assert c.log_level == "DEBUG"
    assert c.hosted_api_key == "SECRET"
    assert c.hosted_mode_enabled is True
    assert c.hosted_api_base == "https://example.test/v1"


def test_config_from_env_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for var in (
        ENV_DB_PATH,
        ENV_DUMPS_DIR,
        ENV_USER_AGENT,
        ENV_HTTP_TIMEOUT,
        ENV_LOG_LEVEL,
        ENV_HOSTED_API_KEY,
        ENV_HOSTED_API_BASE,
    ):
        monkeypatch.delenv(var, raising=False)

    c = Config.from_env()

    assert c.http_timeout_seconds == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert c.log_level == "INFO"
    assert c.hosted_api_key is None
    assert c.hosted_mode_enabled is False


def test_config_from_env_empty_api_key_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_HOSTED_API_KEY, "   ")

    c = Config.from_env()

    assert c.hosted_api_key is None
    assert c.hosted_mode_enabled is False


def test_parse_float_env_invalid_string_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_HTTP_TIMEOUT, "not-a-number")
    with pytest.raises(ValidationError) as exc_info:
        _parse_float_env(ENV_HTTP_TIMEOUT, 30.0)
    assert exc_info.value.details["env_var"] == ENV_HTTP_TIMEOUT
    assert exc_info.value.details["value"] == "not-a-number"


def test_parse_float_env_non_positive_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_HTTP_TIMEOUT, "0")
    with pytest.raises(ValidationError):
        _parse_float_env(ENV_HTTP_TIMEOUT, 30.0)

    monkeypatch.setenv(ENV_HTTP_TIMEOUT, "-5")
    with pytest.raises(ValidationError):
        _parse_float_env(ENV_HTTP_TIMEOUT, 30.0)


def test_parse_float_env_empty_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_HTTP_TIMEOUT, "")
    assert _parse_float_env(ENV_HTTP_TIMEOUT, 17.0) == 17.0
    monkeypatch.delenv(ENV_HTTP_TIMEOUT, raising=False)
    assert _parse_float_env(ENV_HTTP_TIMEOUT, 17.0) == 17.0
