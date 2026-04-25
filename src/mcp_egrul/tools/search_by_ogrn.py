"""Тул `search_by_ogrn`: вернуть `CompanyCard` или `IECard` по ОГРН/ОГРНИП.

По длине валидного ОГРН выбирает таблицу:
    * 13 цифр → `companies` → `CompanyCard`.
    * 15 цифр → `individual_entrepreneurs` → `IECard`.

Hosted-режим — как в `search_by_inn`: запрос идёт в hosted API, локальный
SQLite не трогается.
"""

from __future__ import annotations

from ..context import ServiceContext
from ..schemas import CompanyCard, IECard
from ..validators import assert_valid_ogrn, detect_ogrn_subject_type
from ._cards import build_company_card, build_ie_card


async def search_by_ogrn(ctx: ServiceContext, ogrn: str) -> CompanyCard | IECard:
    normalized = assert_valid_ogrn(ogrn)
    if ctx.hosted_client is not None:
        return await ctx.hosted_client.search_by_ogrn(normalized)

    subject_type = detect_ogrn_subject_type(normalized)
    if subject_type == "legal_entity":
        row = await ctx.store.get_company_by_ogrn(normalized)
        return build_company_card(row, identifier=f"ОГРН {normalized}")

    row = await ctx.store.get_ie_by_ogrnip(normalized)
    return build_ie_card(row, identifier=f"ОГРНИП {normalized}")
