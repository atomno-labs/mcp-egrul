"""Тул `get_founders`: учредители по ИНН юр.лица.

Работает только с юридическими лицами (10-значный ИНН). Для 12-значного
(ИП/физлицо) — `ValidationError`: у ИП нет учредителей в терминах ЕГРЮЛ.
"""

from __future__ import annotations

from ..context import ServiceContext
from ..errors import ValidationError
from ..schemas import Founder
from ..validators import assert_valid_inn, detect_subject_type
from ._cards import build_company_card


async def get_founders(ctx: ServiceContext, inn: str) -> list[Founder]:
    normalized = assert_valid_inn(inn)
    if detect_subject_type(normalized) != "legal_entity":
        raise ValidationError(
            (
                f"ИНН {normalized} принадлежит ИП или физлицу — "
                "у них нет учредителей в ЕГРЮЛ/ЕГРИП."
            ),
            hint="Учредители фиксируются только у юр.лиц (ИНН из 10 цифр).",
            details={"inn": normalized},
        )

    if ctx.hosted_client is not None:
        return await ctx.hosted_client.get_founders(normalized)

    row = await ctx.store.get_company_by_inn(normalized)
    card = build_company_card(row, identifier=f"ИНН {normalized}")
    return card.founders
