"""Тул `get_full_card`: тонкая обёртка над `search_by_inn` / `search_by_ogrn`.

Хотя бы один из `inn` / `ogrn` обязателен. Если переданы оба — используем
`inn` (контракт SPEC §4.1 и избегание конфликтов).

В hosted-режиме идёт в отдельный эндпойнт `/companies/card` (SPEC §5.4.1):
он может использовать оптимизированный pre-JOIN на hosted Pro-стороне
(единый запрос в Postgres с `authorized_capital`, `founders`, `director`
одной карточкой, вместо трёх round-trip'ов). Локально такой оптимизации
нет — делегируем в `search_by_inn`/`search_by_ogrn`.
"""

from __future__ import annotations

from ..context import ServiceContext
from ..errors import ValidationError
from ..schemas import CompanyCard, IECard
from ..validators import assert_valid_inn, assert_valid_ogrn
from .search_by_inn import search_by_inn
from .search_by_ogrn import search_by_ogrn


async def get_full_card(
    ctx: ServiceContext,
    *,
    inn: str | None = None,
    ogrn: str | None = None,
) -> CompanyCard | IECard:
    if inn is None and ogrn is None:
        raise ValidationError(
            "get_full_card требует хотя бы один из параметров: inn или ogrn.",
            hint="Передайте ИНН (10 или 12 цифр) или ОГРН (13 или 15 цифр).",
            details={"inn": inn, "ogrn": ogrn},
        )
    if ctx.hosted_client is not None:
        # Валидируем клиент-саид (экономим round-trip на битом идентификаторе).
        normalized_inn = assert_valid_inn(inn) if inn is not None else None
        normalized_ogrn = (
            assert_valid_ogrn(ogrn) if ogrn is not None and inn is None else None
        )
        return await ctx.hosted_client.get_full_card(
            inn=normalized_inn,
            ogrn=normalized_ogrn,
        )
    if inn is not None:
        return await search_by_inn(ctx, inn)
    assert ogrn is not None
    return await search_by_ogrn(ctx, ogrn)
