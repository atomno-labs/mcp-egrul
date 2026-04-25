"""Тесты FastMCP-слоя `server.py`: `ping`, регистрация тулзов, форматирование
ошибок под контракт AI-клиентов, CLI `main()`.

В `fastmcp` 3.x `mcp.call_tool(...)` возвращает `ToolResult` с атрибутами
`content` (список `TextContent`/... для совместимости с MCP-протоколом) и
`structured_content` (dict напрямую — удобно для юнит-тестов). Мы читаем
`structured_content`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcp_egrul import __version__
from mcp_egrul import server as server_module


def _body(result: Any) -> dict[str, Any]:
    """Извлечь dict из `ToolResult` (fastmcp>=3.x)."""
    assert result.structured_content is not None, (
        f"call_tool не вернул structured_content (content={result.content!r})"
    )
    return result.structured_content


@pytest.fixture
def isolated_ctx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Подготовить чистое окружение: свежий SQLite, hosted выключен, ctx сброшен."""
    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "server.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))
    monkeypatch.delenv("ATOMNO_API_KEY", raising=False)
    server_module._ctx = None


@pytest.mark.asyncio
async def test_ping_returns_ok(isolated_ctx: None) -> None:
    result = await server_module.ping()

    assert result["ok"] is True
    assert result["service"] == "mcp-egrul"
    assert result["version"] == __version__
    assert result["hosted_mode"] is False
    assert result["counts"]["companies"] == 0
    assert result["counts"]["individual_entrepreneurs"] == 0


@pytest.mark.asyncio
async def test_get_ctx_reuses_existing_context(isolated_ctx: None) -> None:
    """Вторичный вызов `_get_ctx` не должен пересоздавать ServiceContext."""
    first = await server_module._get_ctx()
    second = await server_module._get_ctx()
    assert first is second


@pytest.mark.asyncio
async def test_get_ctx_parallel_init_double_check_branch(isolated_ctx: None) -> None:
    """Ветка 76->81 в server.py: параллельные `_get_ctx()` → вторая таска
    заходит в lock уже после того, как первая заполнила `_ctx`, и попадает
    в `if _ctx is None` → False → пропускает блок создания.

    Проверяем явно через `asyncio.gather(..., _get_ctx(), _get_ctx())`:
    обе должны вернуть один и тот же объект, и он должен быть создан
    только один раз (иначе был бы двойной `__aenter__` → двойное соединение).
    """
    import asyncio as _asyncio

    tasks = [server_module._get_ctx() for _ in range(5)]
    results = await _asyncio.gather(*tasks)
    first = results[0]
    assert all(r is first for r in results), (
        "все параллельные вызовы _get_ctx должны возвращать один и тот же ctx"
    )


@pytest.mark.asyncio
async def test_server_exposes_all_seven_tools_plus_ping() -> None:
    """SPEC §4.1: семь тулзов + ping = восемь."""
    tools = await server_module.mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "ping",
        "search_by_inn",
        "search_by_ogrn",
        "search_by_name",
        "get_full_card",
        "get_founders",
        "get_director",
        "bulk_cards",
    }
    assert expected.issubset(names), f"отсутствуют тулзы: {expected - names}"


@pytest.mark.asyncio
async def test_tool_reports_validation_error_as_structured_dict(
    isolated_ctx: None,
) -> None:
    """Невалидный ИНН должен вернуться как структурированный error-dict,
    а не сырое исключение — это контракт для AI-клиентов (SPEC §4.3).
    """
    result = await server_module.mcp.call_tool(
        "search_by_inn", arguments={"inn": "1111111111"}
    )
    body = _body(result)
    assert body["error"] is True
    assert body["code"] == "invalid_input"
    assert "message" in body


@pytest.mark.asyncio
async def test_search_by_ogrn_error_via_call_tool(isolated_ctx: None) -> None:
    result = await server_module.mcp.call_tool(
        "search_by_ogrn", arguments={"ogrn": "9999999999999"}
    )
    body = _body(result)
    assert body["error"] is True
    assert body["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_search_by_name_empty_db_returns_zero_hits(isolated_ctx: None) -> None:
    result = await server_module.mcp.call_tool(
        "search_by_name", arguments={"query": "Сбербанк"}
    )
    body = _body(result)
    assert body.get("error") is not True
    assert body["count"] == 0
    assert body["hits"] == []


@pytest.mark.asyncio
async def test_get_full_card_requires_identifier(isolated_ctx: None) -> None:
    result = await server_module.mcp.call_tool(
        "get_full_card", arguments={}
    )
    body = _body(result)
    assert body["error"] is True
    assert body["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_get_full_card_not_found(isolated_ctx: None) -> None:
    result = await server_module.mcp.call_tool(
        "get_full_card", arguments={"inn": "7707083893"}
    )
    body = _body(result)
    assert body["error"] is True
    assert body["code"] == "not_found"


@pytest.mark.asyncio
async def test_get_founders_not_found(isolated_ctx: None) -> None:
    result = await server_module.mcp.call_tool(
        "get_founders", arguments={"inn": "7707083893"}
    )
    body = _body(result)
    assert body["error"] is True
    assert body["code"] == "not_found"


@pytest.mark.asyncio
async def test_get_director_returns_null_for_missing(isolated_ctx: None) -> None:
    result = await server_module.mcp.call_tool(
        "get_director", arguments={"inn": "7707083893"}
    )
    body = _body(result)
    # not-found по директору НЕ пустой `director: null`, а полноценный error
    # (SPEC §4.3: «если компании нет в БД — это not_found»).
    assert body["error"] is True
    assert body["code"] == "not_found"


@pytest.mark.asyncio
async def test_bulk_cards_returns_structured_partial_result(
    isolated_ctx: None,
) -> None:
    result = await server_module.mcp.call_tool(
        "bulk_cards",
        arguments={"inns": ["7707083893", "1111111111"]},
    )
    body = _body(result)
    assert body.get("error") is not True
    assert body["requested"] == 2
    assert body["found"] == 0
    assert len(body["errors"]) == 2


@pytest.mark.asyncio
async def test_bulk_cards_over_limit_returns_error(isolated_ctx: None) -> None:
    result = await server_module.mcp.call_tool(
        "bulk_cards", arguments={"inns": ["7707083893"] * 101}
    )
    body = _body(result)
    assert body["error"] is True
    assert body["code"] == "bulk_too_large"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_exits_2_on_invalid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_EGRUL_HTTP_TIMEOUT", "not-a-float")
    with pytest.raises(SystemExit) as exc_info:
        server_module.main()
    assert exc_info.value.code == 2


def test_main_invokes_mcp_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "main.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))
    monkeypatch.delenv("ATOMNO_API_KEY", raising=False)

    called: dict[str, bool] = {"ran": False}

    def _fake_run() -> None:
        called["ran"] = True

    monkeypatch.setattr(server_module.mcp, "run", _fake_run)
    server_module.main()
    assert called["ran"] is True


# ---------------------------------------------------------------------------
# Happy-path через call_tool с реальной записью в SQLite — закрывает
# `return card.model_dump(mode="json")` ветки всех 7 тулзов.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_through_call_tool_with_seeded_db(
    isolated_ctx: None,
) -> None:
    """Проложить фикстурную запись в SQLite и прогнать все 7 тулзов по call_tool."""
    from tests.conftest import make_company_row, make_ie_row

    ctx = await server_module._get_ctx()
    await ctx.store.upsert_company(make_company_row())
    await ctx.store.upsert_ie(make_ie_row())

    b1 = _body(
        await server_module.mcp.call_tool(
            "search_by_inn", arguments={"inn": "7707083893"}
        )
    )
    assert b1.get("error") is not True
    assert b1["inn"] == "7707083893"

    b2 = _body(
        await server_module.mcp.call_tool(
            "search_by_ogrn", arguments={"ogrn": "1027700132195"}
        )
    )
    assert b2.get("error") is not True
    assert b2["ogrn"] == "1027700132195"

    b3 = _body(
        await server_module.mcp.call_tool(
            "search_by_name",
            arguments={"query": "СБЕРБАНК", "limit": 10, "only_active": True},
        )
    )
    assert b3.get("error") is not True
    assert b3["count"] >= 1

    b4 = _body(
        await server_module.mcp.call_tool(
            "get_full_card", arguments={"inn": "7707083893"}
        )
    )
    assert b4.get("error") is not True
    assert b4["inn"] == "7707083893"

    b5 = _body(
        await server_module.mcp.call_tool(
            "get_founders", arguments={"inn": "7707083893"}
        )
    )
    assert b5.get("error") is not True
    assert b5["count"] == 1
    assert b5["founders"][0]["type"] == "legal"

    b6 = _body(
        await server_module.mcp.call_tool(
            "get_director", arguments={"inn": "7707083893"}
        )
    )
    assert b6.get("error") is not True
    assert b6["director"] is not None
    assert b6["director"]["fio"] == "Греф Герман Оскарович"

    b7 = _body(
        await server_module.mcp.call_tool(
            "bulk_cards", arguments={"inns": ["7707083893", "500100732259"]}
        )
    )
    assert b7.get("error") is not True
    assert b7["found"] == 2


# ---------------------------------------------------------------------------
# `ping` — error-branch: если `ctx.store.count()` бросает McpEgrulError,
# тул должен вернуть error-dict, а не зарейзить.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_returns_error_dict_when_store_count_fails(
    isolated_ctx: None,
) -> None:
    from mcp_egrul.errors import McpEgrulError

    ctx = await server_module._get_ctx()

    async def _broken_count() -> dict[str, int]:
        raise McpEgrulError(
            "тестовая ошибка SQLite",
            details={"driver_error": "simulated"},
        )

    ctx.store.count = _broken_count  # type: ignore[method-assign]

    result = await server_module.ping()
    assert result["error"] is True
    assert "message" in result
    assert "SQLite" in result["message"]


# ---------------------------------------------------------------------------
# `_close_ctx_atexit` — прямой вызов на установленном ctx.
# ---------------------------------------------------------------------------


def test_close_ctx_atexit_is_noop_when_ctx_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_module, "_ctx", None)
    server_module._close_ctx_atexit()


def test_close_ctx_atexit_runs_aexit_on_set_ctx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если `_ctx` установлен — atexit-хук запускает `__aexit__` в новом loop."""
    called: dict[str, bool] = {"aexit": False}

    class _FakeCtx:
        async def __aexit__(self, *exc_info: object) -> None:
            called["aexit"] = True

    monkeypatch.setattr(server_module, "_ctx", _FakeCtx())
    server_module._close_ctx_atexit()
    assert called["aexit"] is True


# ---------------------------------------------------------------------------
# ServiceContext: идемпотентность __aenter__ / __aexit__.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_director_returns_null_when_company_has_no_director(
    isolated_ctx: None,
) -> None:
    """Закрывает ветку `if director is None: return {"inn": inn, "director": None}`.

    Для этого нужна компания БЕЗ `data_json.director` — seed фикстуру с None.
    """
    from tests.conftest import make_company_row

    ctx = await server_module._get_ctx()
    row = make_company_row(data_json={"okved_additional": []})
    await ctx.store.upsert_company(row)

    result = await server_module.mcp.call_tool(
        "get_director", arguments={"inn": "7707083893"}
    )
    body = _body(result)
    assert body.get("error") is not True
    assert body["inn"] == "7707083893"
    assert body["director"] is None


@pytest.mark.asyncio
async def test_search_by_name_propagates_mcp_error_to_structured_dict(
    isolated_ctx: None,
) -> None:
    """Если нижележащий store бросает McpEgrulError — тул отдаёт error-dict."""
    from mcp_egrul.errors import McpEgrulError

    ctx = await server_module._get_ctx()

    async def _broken_search(
        query: str, limit: int = 10, only_active: bool = False
    ) -> list[dict]:
        raise McpEgrulError(
            "симулированный сбой FTS",
            details={"query": query},
        )

    ctx.store.search_companies_by_name = _broken_search  # type: ignore[method-assign]

    result = await server_module.mcp.call_tool(
        "search_by_name",
        arguments={"query": "anything", "limit": 5},
    )
    body = _body(result)
    assert body["error"] is True
    assert "FTS" in body["message"]


@pytest.mark.asyncio
async def test_service_context_reentry_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Второй вызов `__aenter__` не должен повторно инициализировать store.

    И двойной `__aexit__` не должен падать (после первого `_entered=False`).
    """
    from mcp_egrul.config import Config
    from mcp_egrul.context import ServiceContext
    from mcp_egrul.db import SQLiteStore

    monkeypatch.delenv("ATOMNO_API_KEY", raising=False)

    cfg = Config(
        db_path=tmp_path / "reentry.sqlite",
        dumps_dir=tmp_path / "dumps",
        user_agent="mcp-egrul-test/0.1",
        http_timeout_seconds=5.0,
        log_level="INFO",
        hosted_api_key=None,
        hosted_api_base="https://api.atomno.ru/mcp-egrul/v1",
    )
    store = SQLiteStore(cfg.db_path)
    ctx = ServiceContext.for_testing(store=store, config=cfg)

    assert ctx._entered is False
    await ctx.__aenter__()
    assert ctx._entered is True
    await ctx.__aenter__()  # повторно — должен быть noop
    assert ctx._entered is True

    await ctx.__aexit__(None, None, None)
    assert ctx._entered is False
    await ctx.__aexit__(None, None, None)  # повторно — noop
    assert ctx._entered is False
