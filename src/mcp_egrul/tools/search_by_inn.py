"""Тул `search_by_inn`: вернуть `CompanyCard` или `IECard` по ИНН.

По длине валидного ИНН автоматически выбирает таблицу:
    * 10 цифр → `companies` → `CompanyCard`.
    * 12 цифр → `individual_entrepreneurs` → `IECard`.

В hosted-режиме (`ATOMNO_API_KEY` задан) запрос идёт в `api.atomno.ru`
через `HostedClient` — локальный SQLite не трогается. Silent fallback
на локальный слепок запрещён: иначе AI-агент подумает что получил
свежие данные от Pro, а на деле они из устаревшего дампа.

Ошибки:
    * `ValidationError` — ИНН не прошёл контрольную цифру.
    * `NotFoundError`   — запись отсутствует в локальном слепке / hosted API.
    * `HostedAuthError` / `RateLimitedError` / `SourceUnavailableError`
      — из hosted API (только в hosted-режиме).
"""

from __future__ import annotations

from ..context import ServiceContext
from ..schemas import CompanyCard, IECard
from ..validators import assert_valid_inn, detect_subject_type
from ._cards import build_company_card, build_ie_card


async def search_by_inn(ctx: ServiceContext, inn: str) -> CompanyCard | IECard:
    normalized = assert_valid_inn(inn)
    if ctx.hosted_client is not None:
        return await ctx.hosted_client.search_by_inn(normalized)

    subject_type = detect_subject_type(normalized)
    if subject_type == "legal_entity":
        row = await ctx.store.get_company_by_inn(normalized)
        return build_company_card(row, identifier=f"ИНН {normalized}")

    row = await ctx.store.get_ie_by_inn(normalized)
    return build_ie_card(row, identifier=f"ИНН {normalized}")
