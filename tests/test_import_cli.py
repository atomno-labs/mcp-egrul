"""Тесты CLI `mcp-egrul-import` (Phase 1 — реальный импорт)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mcp_egrul.scripts.import_opendata import main

FIXTURES = Path(__file__).parent / "fixtures"


def _prepare_dumps_dir(root: Path, registry: str, iso_date: str) -> None:
    target = root / registry / iso_date
    target.mkdir(parents=True, exist_ok=True)
    fixture = FIXTURES / f"{registry}_sample.xml"
    shutil.copy(fixture, target / fixture.name)


def test_import_cli_runs_full_egrul(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "import.sqlite"
    dumps_dir = tmp_path / "dumps"
    _prepare_dumps_dir(dumps_dir, "egrul", "2026-04-01")
    monkeypatch.setenv("MCP_EGRUL_DB", str(db_path))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(dumps_dir))

    exit_code = main(["--registry", "egrul", "--full"])
    assert exit_code == 0
    assert db_path.exists()


def test_import_cli_incremental_returns_nothing_to_do_on_second_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "import.sqlite"
    dumps_dir = tmp_path / "dumps"
    _prepare_dumps_dir(dumps_dir, "egrul", "2026-04-01")
    monkeypatch.setenv("MCP_EGRUL_DB", str(db_path))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(dumps_dir))

    first = main(["--registry", "egrul", "--full"])
    assert first == 0
    second = main(["--registry", "egrul", "--incremental"])
    assert second == 5


def test_import_cli_source_error_returns_exit_4(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "import.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))

    exit_code = main(["--registry", "egrul"])
    assert exit_code == 4


def test_import_cli_requires_registry_arg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "import.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))

    with pytest.raises(SystemExit):
        main([])


def test_import_cli_returns_exit_2_on_invalid_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Невалидный HTTP-timeout → Config.from_env кидает ValidationError → exit 2."""
    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "import.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))
    monkeypatch.setenv("MCP_EGRUL_HTTP_TIMEOUT", "not-a-float")

    exit_code = main(["--registry", "egrul", "--full"])
    assert exit_code == 2


def test_import_cli_nothing_to_import_without_hint_returns_exit_5(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ветка `if exc.hint:` = False для NothingToImportError (строки 90->92):
    CLI всё равно отдаёт exit-code 5, просто без hint в логе.
    """
    from mcp_egrul.errors import NothingToImportError
    from mcp_egrul.sources import opendata as opendata_module

    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "import.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))

    async def _raise_no_hint(*_args: object, **_kwargs: object) -> None:
        raise NothingToImportError("нет новых выгрузок", hint=None)

    monkeypatch.setattr(
        opendata_module.OpenDataSource, "run_ingest", _raise_no_hint
    )
    exit_code = main(["--registry", "egrul", "--incremental"])
    assert exit_code == 5


def test_import_cli_source_error_without_hint_returns_exit_4(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ветка `if exc.hint:` = False для McpEgrulError (строки 95->97)."""
    from mcp_egrul.errors import McpEgrulError
    from mcp_egrul.sources import opendata as opendata_module

    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "import.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))

    async def _raise_no_hint(*_args: object, **_kwargs: object) -> None:
        raise McpEgrulError("база упала", hint=None)

    monkeypatch.setattr(
        opendata_module.OpenDataSource, "run_ingest", _raise_no_hint
    )
    exit_code = main(["--registry", "egrul", "--full"])
    assert exit_code == 4
