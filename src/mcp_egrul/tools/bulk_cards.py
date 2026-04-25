"""Тул `bulk_cards`: массовая проверка до `BULK_CARDS_MAX_INNS` ИНН.

Контракт ответа (`BulkResult`):
    * `cards`    — список успешно собранных карточек (юр.лиц или ИП).
    * `errors`   — список точечных ошибок по конкретным ИНН
                   (валидация / not_found) — остальные ИНН это не ломает.
    * `requested`/`found` — для быстрого UI-счётчика.

Превышение лимита — `BulkTooLargeError` (не режется молча).

В hosted-режиме делегируем в `HostedClient.bulk_cards()` — там bulk
реализован одним POST-запросом, без rate-limit per-item.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..constants import BULK_CARDS_MAX_INNS
from ..context import ServiceContext
from ..errors import BulkTooLargeError, McpEgrulError, ValidationError
from ..schemas import BulkItemError, BulkResult, CompanyCard, IECard
from .search_by_inn import search_by_inn


async def bulk_cards(ctx: ServiceContext, inns: list[str]) -> BulkResult:
    if not isinstance(inns, list):
        raise ValidationError(
            f"bulk_cards требует список ИНН, получено: {type(inns).__name__}.",
            details={"input_type": type(inns).__name__},
        )
    if len(inns) == 0:
        raise ValidationError(
            "bulk_cards: список ИНН пуст.",
            hint=f"Передайте 1..{BULK_CARDS_MAX_INNS} ИНН.",
            details={"count": 0},
        )
    if len(inns) > BULK_CARDS_MAX_INNS:
        raise BulkTooLargeError(
            (
                f"bulk_cards: запрошено {len(inns)} ИНН, максимум "
                f"{BULK_CARDS_MAX_INNS}."
            ),
            hint=(
                f"Разбейте на батчи по {BULK_CARDS_MAX_INNS} ИНН или "
                "используйте hosted Pro (без rate-limit)."
            ),
            details={"count": len(inns), "max": BULK_CARDS_MAX_INNS},
        )

    if ctx.hosted_client is not None:
        return await ctx.hosted_client.bulk_cards(inns)

    results = await asyncio.gather(
        *(_fetch_one(ctx, i) for i in inns),
        return_exceptions=False,
    )

    cards: list[CompanyCard | IECard] = []
    errors: list[BulkItemError] = []
    for outcome in results:
        if isinstance(outcome, BulkItemError):
            errors.append(outcome)
        else:
            cards.append(outcome)

    return BulkResult(
        cards=cards,
        errors=errors,
        requested=len(inns),
        found=len(cards),
    )


async def _fetch_one(ctx: ServiceContext, inn: str) -> Any:
    """Одна запись в bulk: CompanyCard/IECard либо BulkItemError.

    Ошибки тул-слоя (`McpEgrulError`) упаковываются в `BulkItemError` —
    весь bulk не падает из-за одного плохого ИНН. Всё остальное
    (неожиданные исключения) пробрасываем наружу — это уже не "точечная"
    проблема, а системная (DB сломалась и т.п.).
    """
    try:
        return await search_by_inn(ctx, inn)
    except McpEgrulError as exc:
        return BulkItemError(
            inn=inn if isinstance(inn, str) else str(inn),
            code=exc.code,
            message=exc.message_ru,
        )
