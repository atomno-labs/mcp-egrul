"""Тул `get_director`: текущий руководитель юр.лица по ИНН.

Только для 10-значного ИНН (юр.лицо). Если у компании нет актуального
руководителя в реестре — возвращает `None` (валидный ответ).
"""

from __future__ import annotations

from ..context import ServiceContext
from ..errors import ValidationError
from ..schemas import Director
from ..validators import assert_valid_inn, detect_subject_type
from ._cards import build_company_card


async def get_director(ctx: ServiceContext, inn: str) -> Director | None:
    normalized = assert_valid_inn(inn)
    if detect_subject_type(normalized) != "legal_entity":
        raise ValidationError(
            (
                f"ИНН {normalized} принадлежит ИП или физлицу — "
                "у них нет руководителя в ЕГРЮЛ/ЕГРИП."
            ),
            hint="Руководитель фиксируется только у юр.лиц (ИНН из 10 цифр).",
            details={"inn": normalized},
        )

    if ctx.hosted_client is not None:
        return await ctx.hosted_client.get_director(normalized)

    row = await ctx.store.get_company_by_inn(normalized)
    card = build_company_card(row, identifier=f"ИНН {normalized}")
    return card.director
