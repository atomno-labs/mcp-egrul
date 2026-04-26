"""Тесты CLI-обвязки `atomno-mcp-egrul` (argparse + log-level + transport).

Проверяет, что:
  * `--help` / `--version` выходят с exit-code 0 без запуска MCP-сервера.
  * `--transport` валидирует choices.
  * `--log-level` имеет приоритет над env-переменной MCP_EGRUL_LOG_LEVEL.
  * Невалидный env валит процесс с exit-code 2 (loud-fail, без silent-INFO-fallback).
  * Дефолты `--host=127.0.0.1`, `--port=8000` корректны.

Тесты НЕ запускают `mcp.run()` — он мокается через `monkeypatch`. Это и есть
смысл аргумента `argv` у `main()`: testable CLI без подключения к stdin.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from mcp_egrul import __version__, server


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Изолировать тесты CLI от глобального env: убираем MCP_EGRUL_*, дать tmp DB."""
    for var in (
        "MCP_EGRUL_LOG_LEVEL",
        "MCP_EGRUL_DB_PATH",
        "MCP_EGRUL_DUMPS_DIR",
        "ATOMNO_API_KEY",
        "ATOMNO_API_BASE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MCP_EGRUL_DB_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Подменить `mcp.run` записывающим заглушкой — тесты не запускают сервер."""
    captured: dict[str, Any] = {"called": False, "kwargs": None}

    def _fake_run(**kwargs: Any) -> None:
        captured["called"] = True
        captured["kwargs"] = kwargs

    monkeypatch.setattr(server.mcp, "run", _fake_run)
    return captured


@pytest.fixture
def stub_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменить ServiceContext.from_env заглушкой — не дёргать SQLite/диск."""
    class _StubConfig:
        db_path = Path("/tmp/stub.sqlite")
        hosted_mode_enabled = False
        log_level = "INFO"

    class _StubContext:
        config = _StubConfig()

    monkeypatch.setattr(
        server.ServiceContext,
        "from_env",
        classmethod(lambda cls: _StubContext()),
    )


# ---------------------------------------------------------------------------
# --help / --version — должны выйти БЕЗ запуска mcp.run()
# ---------------------------------------------------------------------------


class TestHelp:
    def test_long_flag_exits_zero(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--help"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "atomno-mcp-egrul" in out
        assert "--transport" in out
        assert "--version" in out
        assert fake_run["called"] is False

    def test_short_flag_exits_zero(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["-h"])
        assert exc_info.value.code == 0
        assert fake_run["called"] is False


class TestVersion:
    def test_long_flag(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out == f"atomno-mcp-egrul {__version__}"
        assert fake_run["called"] is False

    def test_short_flag(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["-V"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out == f"atomno-mcp-egrul {__version__}"

    def test_version_string_matches_package(self) -> None:
        """Защита от рассинхрона __init__.py vs pyproject.toml."""
        assert __version__ == server.__version__


# ---------------------------------------------------------------------------
# --transport — выбор транспорта.
# ---------------------------------------------------------------------------


class TestTransportValidation:
    def test_default_is_stdio(
        self, fake_run: dict[str, Any], stub_context: None
    ) -> None:
        rc = server.main([])
        assert rc == 0
        assert fake_run["kwargs"] == {"transport": "stdio"}

    def test_explicit_stdio(
        self, fake_run: dict[str, Any], stub_context: None
    ) -> None:
        rc = server.main(["--transport", "stdio"])
        assert rc == 0
        assert fake_run["kwargs"] == {"transport": "stdio"}

    @pytest.mark.parametrize("transport", ["http", "sse", "streamable-http"])
    def test_http_transports_pass_host_port(
        self, transport: str, fake_run: dict[str, Any], stub_context: None
    ) -> None:
        rc = server.main([
            "--transport", transport,
            "--host", "0.0.0.0",
            "--port", "9000",
        ])
        assert rc == 0
        assert fake_run["kwargs"] == {
            "transport": transport,
            "host": "0.0.0.0",
            "port": 9000,
        }

    def test_invalid_transport_exits_two(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--transport", "websocket"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "websocket" in err.lower() or "invalid choice" in err.lower()
        assert fake_run["called"] is False


# ---------------------------------------------------------------------------
# --log-level — приоритет CLI над env, валидация env.
# ---------------------------------------------------------------------------


class TestLogLevelPrecedence:
    def test_cli_overrides_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_run: dict[str, Any],
        stub_context: None,
    ) -> None:
        monkeypatch.setenv("MCP_EGRUL_LOG_LEVEL", "WARNING")
        rc = server.main(["--log-level", "DEBUG"])
        assert rc == 0
        assert logging.getLogger().level == logging.DEBUG

    def test_env_when_no_cli(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_run: dict[str, Any],
        stub_context: None,
    ) -> None:
        monkeypatch.setenv("MCP_EGRUL_LOG_LEVEL", "WARNING")
        rc = server.main([])
        assert rc == 0
        assert logging.getLogger().level == logging.WARNING

    def test_default_info(
        self, fake_run: dict[str, Any], stub_context: None
    ) -> None:
        rc = server.main([])
        assert rc == 0
        assert logging.getLogger().level == logging.INFO

    def test_env_case_normalized(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_run: dict[str, Any],
        stub_context: None,
    ) -> None:
        monkeypatch.setenv("MCP_EGRUL_LOG_LEVEL", "  warning  ")
        rc = server.main([])
        assert rc == 0
        assert logging.getLogger().level == logging.WARNING


class TestInvalidEnvBailsOutCleanly:
    def test_invalid_env_exits_two(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        fake_run: dict[str, Any],
    ) -> None:
        monkeypatch.setenv("MCP_EGRUL_LOG_LEVEL", "TRACE")
        rc = server.main([])
        assert rc == 2
        assert fake_run["called"] is False
        err = capsys.readouterr().err
        assert "MCP_EGRUL_LOG_LEVEL" in err

    def test_invalid_cli_log_level_argparse_rejects(
        self, capsys: pytest.CaptureFixture[str], fake_run: dict[str, Any]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            server.main(["--log-level", "TRACE"])
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "TRACE" in err or "invalid choice" in err.lower()


# ---------------------------------------------------------------------------
# Парсер защищает дефолты host/port.
# ---------------------------------------------------------------------------


class TestParserDefaults:
    def test_host_default_is_localhost(self) -> None:
        ns = server._build_arg_parser().parse_args([])
        assert ns.host == "127.0.0.1"

    def test_port_default_is_8000(self) -> None:
        ns = server._build_arg_parser().parse_args([])
        assert ns.port == 8000

    def test_port_parsed_as_int(self) -> None:
        ns = server._build_arg_parser().parse_args(["--port", "12345"])
        assert ns.port == 12345
        assert isinstance(ns.port, int)
