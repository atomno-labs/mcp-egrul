"""FastMCP entrypoint mcp-egrul.

Регистрирует восемь тулзов:
    * `ping`              — диагностика.
    * `search_by_inn`     — SPEC §4.1.
    * `search_by_ogrn`    — SPEC §4.1.
    * `search_by_name`    — SPEC §4.1.
    * `get_full_card`     — SPEC §4.1.
    * `get_founders`      — SPEC §4.1.
    * `get_director`      — SPEC §4.1.
    * `bulk_cards`        — SPEC §4.1.

`ServiceContext` создаётся лениво на первом вызове и переиспользуется
на весь процесс. Закрытие SQLite-коннектов — через `atexit`-хук.

Логирование уважает `MCP_EGRUL_LOG_LEVEL` (см. `.env.example`). Ошибки
обрабатываются ТОЛЬКО через `McpEgrulError.to_dict()` — никаких silent
fallback или «вернули null».
"""

from __future__ import annotations

import asyncio
import atexit
import logging
from typing import Any

from fastmcp import FastMCP

from . import __version__
from .context import ServiceContext
from .errors import McpEgrulError, ValidationError
from .tools import (
    bulk_cards as _bulk_cards_impl,
)
from .tools import (
    get_director as _get_director_impl,
)
from .tools import (
    get_founders as _get_founders_impl,
)
from .tools import (
    get_full_card as _get_full_card_impl,
)
from .tools import (
    search_by_inn as _search_by_inn_impl,
)
from .tools import (
    search_by_name as _search_by_name_impl,
)
from .tools import (
    search_by_ogrn as _search_by_ogrn_impl,
)

logger = logging.getLogger("mcp_egrul")

mcp: FastMCP = FastMCP(
    name="mcp-egrul",
    instructions=(
        "MCP-сервер для ЕГРЮЛ/ЕГРИП РФ. Семь тулзов: search_by_inn, "
        "search_by_ogrn, search_by_name, get_full_card, get_founders, "
        "get_director, bulk_cards. Источник — официальные open-data "
        "дампы ФНС; в hosted-режиме — api.atomno.ru."
    ),
)

_ctx: ServiceContext | None = None
_ctx_lock = asyncio.Lock()


async def _get_ctx() -> ServiceContext:
    global _ctx
    if _ctx is not None:
        return _ctx
    async with _ctx_lock:
        if _ctx is None:
            ctx = ServiceContext.from_env()
            await ctx.__aenter__()
            _ctx = ctx
            atexit.register(_close_ctx_atexit)
    assert _ctx is not None
    return _ctx


def _close_ctx_atexit() -> None:
    if _ctx is None:
        return
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_ctx.__aexit__(None, None, None))
        loop.close()
    except Exception:  # pragma: no cover - best-effort cleanup
        pass


def _err(exc: McpEgrulError) -> dict[str, Any]:
    return exc.to_dict()


# ---------------------------------------------------------------------------
# Тулзы MCP.
# ---------------------------------------------------------------------------


@mcp.tool()
async def ping() -> dict[str, Any]:
    """Диагностика: сервер жив, сообщает версию и размер локального слепка."""
    ctx = await _get_ctx()
    try:
        counts = await ctx.store.count()
    except McpEgrulError as exc:
        return _err(exc)
    return {
        "ok": True,
        "service": "mcp-egrul",
        "version": __version__,
        "db_path": str(ctx.config.db_path),
        "hosted_mode": ctx.config.hosted_mode_enabled,
        "counts": counts,
    }


@mcp.tool()
async def search_by_inn(inn: str) -> dict[str, Any]:
    """Карточка юр.лица или ИП по ИНН (10 цифр — ООО/АО, 12 — ИП/физлицо)."""
    try:
        ctx = await _get_ctx()
        card = await _search_by_inn_impl(ctx, inn)
        return card.model_dump(mode="json")
    except McpEgrulError as exc:
        return _err(exc)


@mcp.tool()
async def search_by_ogrn(ogrn: str) -> dict[str, Any]:
    """Карточка по ОГРН (13 цифр) или ОГРНИП (15 цифр)."""
    try:
        ctx = await _get_ctx()
        card = await _search_by_ogrn_impl(ctx, ogrn)
        return card.model_dump(mode="json")
    except McpEgrulError as exc:
        return _err(exc)


@mcp.tool()
async def search_by_name(
    query: str,
    limit: int = 10,
    only_active: bool = False,
) -> dict[str, Any]:
    """Fuzzy-поиск юр.лиц по названию через FTS5.

    Args:
        query: строка запроса (минимум 2 символа).
        limit: максимум результатов (1..50).
        only_active: фильтровать только записи со статусом 'active'.
    """
    try:
        ctx = await _get_ctx()
        hits = await _search_by_name_impl(
            ctx, query, limit=limit, only_active=only_active
        )
        return {"hits": [h.model_dump(mode="json") for h in hits], "count": len(hits)}
    except McpEgrulError as exc:
        return _err(exc)


@mcp.tool()
async def get_full_card(
    inn: str | None = None,
    ogrn: str | None = None,
) -> dict[str, Any]:
    """Полная карточка (все секции: реквизиты, ОКВЭД, учредители, директор).

    Хотя бы один из `inn` / `ogrn` обязателен. Если переданы оба — используется `inn`.
    """
    try:
        ctx = await _get_ctx()
        card = await _get_full_card_impl(ctx, inn=inn, ogrn=ogrn)
        return card.model_dump(mode="json")
    except McpEgrulError as exc:
        return _err(exc)


@mcp.tool()
async def get_founders(inn: str) -> dict[str, Any]:
    """Учредители юр.лица по ИНН (только 10-значный ИНН)."""
    try:
        ctx = await _get_ctx()
        founders = await _get_founders_impl(ctx, inn)
        return {
            "inn": inn,
            "founders": [f.model_dump(mode="json") for f in founders],
            "count": len(founders),
        }
    except McpEgrulError as exc:
        return _err(exc)


@mcp.tool()
async def get_director(inn: str) -> dict[str, Any]:
    """Текущий руководитель юр.лица по ИНН (только 10-значный ИНН)."""
    try:
        ctx = await _get_ctx()
        director = await _get_director_impl(ctx, inn)
        if director is None:
            return {"inn": inn, "director": None}
        return {"inn": inn, "director": director.model_dump(mode="json")}
    except McpEgrulError as exc:
        return _err(exc)


@mcp.tool()
async def bulk_cards(inns: list[str]) -> dict[str, Any]:
    """Массовая выгрузка до 100 карточек за один вызов.

    Вернёт объект с полями `cards` (успешные) и `errors` (точечные ошибки
    по отдельным ИНН) — один плохой ИНН не ломает весь bulk.
    """
    try:
        ctx = await _get_ctx()
        result = await _bulk_cards_impl(ctx, inns)
        return result.model_dump(mode="json")
    except McpEgrulError as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Точка входа CLI.
# ---------------------------------------------------------------------------


def main() -> None:
    """Запустить FastMCP по stdio."""
    try:
        ctx_template = ServiceContext.from_env()
    except ValidationError as exc:
        logging.basicConfig(level="INFO")
        logger.error("mcp-egrul: невалидная конфигурация — %s", exc.message_ru)
        raise SystemExit(2) from exc

    log_level = ctx_template.config.log_level
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "mcp-egrul %s starting (db=%s, hosted=%s)",
        __version__,
        ctx_template.config.db_path,
        ctx_template.config.hosted_mode_enabled,
    )
    mcp.run()


if __name__ == "__main__":
    main()
