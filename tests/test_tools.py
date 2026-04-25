"""Тесты тулзов mcp-egrul против пустой и заполненной БД."""

from __future__ import annotations

import pytest

from mcp_egrul.context import ServiceContext
from mcp_egrul.errors import (
    BulkTooLargeError,
    NotFoundError,
    ValidationError,
)
from mcp_egrul.tools.bulk_cards import bulk_cards
from mcp_egrul.tools.get_director import get_director
from mcp_egrul.tools.get_founders import get_founders
from mcp_egrul.tools.get_full_card import get_full_card
from mcp_egrul.tools.search_by_inn import search_by_inn
from mcp_egrul.tools.search_by_name import search_by_name
from mcp_egrul.tools.search_by_ogrn import search_by_ogrn
from tests.conftest import make_company_row, make_ie_row

# ---------------------------------------------------------------------------
# search_by_inn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_inn_invalid(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await search_by_inn(ctx, "1111111111")


@pytest.mark.asyncio
async def test_search_by_inn_not_found_legal(ctx: ServiceContext) -> None:
    with pytest.raises(NotFoundError):
        await search_by_inn(ctx, "7707083893")


@pytest.mark.asyncio
async def test_search_by_inn_not_found_ie(ctx: ServiceContext) -> None:
    with pytest.raises(NotFoundError):
        await search_by_inn(ctx, "500100732259")


@pytest.mark.asyncio
async def test_search_by_inn_found_legal(ctx: ServiceContext) -> None:
    await ctx.store.upsert_company(make_company_row())
    card = await search_by_inn(ctx, "7707083893")
    assert card.inn == "7707083893"
    assert card.name_short == "ПАО СБЕРБАНК"


@pytest.mark.asyncio
async def test_search_by_inn_found_ie(ctx: ServiceContext) -> None:
    await ctx.store.upsert_ie(make_ie_row())
    card = await search_by_inn(ctx, "500100732259")
    assert card.inn == "500100732259"
    assert card.fio == "Иванов Иван Иванович"


# ---------------------------------------------------------------------------
# search_by_ogrn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_ogrn_invalid(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await search_by_ogrn(ctx, "1234567890123")


@pytest.mark.asyncio
async def test_search_by_ogrn_found_legal(ctx: ServiceContext) -> None:
    await ctx.store.upsert_company(make_company_row())
    card = await search_by_ogrn(ctx, "1027700132195")
    assert card.inn == "7707083893"


@pytest.mark.asyncio
async def test_search_by_ogrnip_found_ie(ctx: ServiceContext) -> None:
    await ctx.store.upsert_ie(make_ie_row())
    card = await search_by_ogrn(ctx, "304500116000061")
    assert card.ogrnip == "304500116000061"


# ---------------------------------------------------------------------------
# search_by_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_name_empty_db(ctx: ServiceContext) -> None:
    hits = await search_by_name(ctx, "Сбер")
    assert hits == []


@pytest.mark.asyncio
async def test_search_by_name_finds(ctx: ServiceContext) -> None:
    await ctx.store.upsert_company(make_company_row())
    hits = await search_by_name(ctx, "Сбер")
    assert len(hits) == 1
    assert hits[0].inn == "7707083893"
    assert 0.0 < hits[0].relevance_score <= 1.0


@pytest.mark.asyncio
async def test_search_by_name_too_short(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await search_by_name(ctx, "С")


@pytest.mark.asyncio
async def test_search_by_name_limit_too_large(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await search_by_name(ctx, "Сбер", limit=9999)


@pytest.mark.asyncio
async def test_search_by_name_limit_non_positive(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await search_by_name(ctx, "Сбер", limit=0)


# ---------------------------------------------------------------------------
# get_full_card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_full_card_requires_id(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await get_full_card(ctx)


@pytest.mark.asyncio
async def test_get_full_card_by_inn(ctx: ServiceContext) -> None:
    await ctx.store.upsert_company(make_company_row())
    card = await get_full_card(ctx, inn="7707083893")
    assert card.ogrn == "1027700132195"


@pytest.mark.asyncio
async def test_get_full_card_by_ogrn(ctx: ServiceContext) -> None:
    await ctx.store.upsert_company(make_company_row())
    card = await get_full_card(ctx, ogrn="1027700132195")
    assert card.inn == "7707083893"


# ---------------------------------------------------------------------------
# get_founders / get_director
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_founders_rejects_ie_inn(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await get_founders(ctx, "500100732259")


@pytest.mark.asyncio
async def test_get_founders_returns_list(ctx: ServiceContext) -> None:
    await ctx.store.upsert_company(make_company_row())
    founders = await get_founders(ctx, "7707083893")
    assert len(founders) == 1
    assert founders[0].name == "Центральный банк Российской Федерации"
    assert founders[0].share_percent == 50.0


@pytest.mark.asyncio
async def test_get_director_rejects_ie_inn(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await get_director(ctx, "500100732259")


@pytest.mark.asyncio
async def test_get_director_returns_director(ctx: ServiceContext) -> None:
    await ctx.store.upsert_company(make_company_row())
    director = await get_director(ctx, "7707083893")
    assert director is not None
    assert director.fio == "Греф Герман Оскарович"


# ---------------------------------------------------------------------------
# bulk_cards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_cards_empty_list_raises(ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await bulk_cards(ctx, [])


@pytest.mark.asyncio
async def test_bulk_cards_non_list_input_raises(ctx: ServiceContext) -> None:
    """Передача не-списка (строка/tuple) должна быть явной ValidationError.

    Цель — явно различать «пустой список» и «передан не list» в telemetry:
    тул НЕ угадывает и НЕ приводит tuple/str/dict к list (no silent fallback).
    """
    with pytest.raises(ValidationError) as exc_info:
        await bulk_cards(ctx, "7707083893")  # type: ignore[arg-type]
    assert exc_info.value.details["input_type"] == "str"

    with pytest.raises(ValidationError) as exc_info:
        await bulk_cards(ctx, ("7707083893",))  # type: ignore[arg-type]
    assert exc_info.value.details["input_type"] == "tuple"


@pytest.mark.asyncio
async def test_bulk_cards_exceeds_max(ctx: ServiceContext) -> None:
    with pytest.raises(BulkTooLargeError):
        await bulk_cards(ctx, ["7707083893"] * 101)


@pytest.mark.asyncio
async def test_bulk_cards_partial(ctx: ServiceContext) -> None:
    """Часть ИНН — валидные+найденные, часть — битые, часть — not_found.

    Ни один плохой ИНН не ломает весь bulk; в ответе — раздельно cards и errors.
    """
    await ctx.store.upsert_company(make_company_row())
    result = await bulk_cards(
        ctx,
        [
            "7707083893",    # found
            "1111111111",    # invalid (bad checksum)
            "7728168971",    # valid but not_found
        ],
    )
    assert result.requested == 3
    assert result.found == 1
    assert len(result.cards) == 1
    assert len(result.errors) == 2
    codes = {e.code for e in result.errors}
    assert "invalid_input" in codes
    assert "not_found" in codes
