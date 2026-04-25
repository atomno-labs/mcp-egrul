"""Тесты SQLiteStore: схема, upsert, поиск по FTS5."""

from __future__ import annotations

import pytest

from mcp_egrul.db import SQLiteStore
from tests.conftest import make_company_row, make_ie_row


@pytest.mark.asyncio
async def test_init_is_idempotent(store: SQLiteStore) -> None:
    await store.init()  # уже был init в фикстуре
    await store.init()
    counts = await store.count()
    assert counts["companies"] == 0
    assert counts["individual_entrepreneurs"] == 0


@pytest.mark.asyncio
async def test_upsert_and_read_company(store: SQLiteStore) -> None:
    row = make_company_row()
    await store.upsert_company(row)

    by_inn = await store.get_company_by_inn(row["inn"])
    assert by_inn is not None
    assert by_inn["inn"] == row["inn"]
    assert by_inn["name_short"] == row["name_short"]
    assert isinstance(by_inn["data_json"], dict)
    assert by_inn["data_json"]["director"]["fio"] == "Греф Герман Оскарович"

    by_ogrn = await store.get_company_by_ogrn(row["ogrn"])
    assert by_ogrn is not None
    assert by_ogrn["inn"] == row["inn"]


@pytest.mark.asyncio
async def test_company_not_found(store: SQLiteStore) -> None:
    assert await store.get_company_by_inn("1111111111") is None
    assert await store.get_company_by_ogrn("1111111111111") is None


@pytest.mark.asyncio
async def test_upsert_updates_existing(store: SQLiteStore) -> None:
    await store.upsert_company(make_company_row())
    await store.upsert_company(make_company_row(name_short="ПАО СБЕР"))
    got = await store.get_company_by_inn("7707083893")
    assert got is not None
    assert got["name_short"] == "ПАО СБЕР"


@pytest.mark.asyncio
async def test_upsert_and_read_ie(store: SQLiteStore) -> None:
    row = make_ie_row()
    await store.upsert_ie(row)
    by_inn = await store.get_ie_by_inn(row["inn"])
    assert by_inn is not None
    assert by_inn["fio"] == "Иванов Иван Иванович"


@pytest.mark.asyncio
async def test_fts_search_finds_company_by_name(store: SQLiteStore) -> None:
    await store.upsert_company(make_company_row())
    hits = await store.search_companies_by_name("Сбербанк", limit=10, only_active=False)
    assert len(hits) == 1
    assert hits[0]["inn"] == "7707083893"
    assert hits[0]["kind"] == "company"
    assert 0.0 < hits[0]["relevance_score"] <= 1.0


@pytest.mark.asyncio
async def test_fts_prefix_search(store: SQLiteStore) -> None:
    await store.upsert_company(make_company_row())
    hits = await store.search_companies_by_name("Сберба", limit=10, only_active=False)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_fts_only_active_filter(store: SQLiteStore) -> None:
    await store.upsert_company(make_company_row())
    await store.upsert_company(
        make_company_row(
            inn="7728168971",
            ogrn="1037700013020",
            name_short="ПАО ГАЗПРОМ",
            name_full="Публичное акционерное общество Газпром",
            status="liquidated",
        )
    )
    all_hits = await store.search_companies_by_name(
        "ПАО", limit=10, only_active=False
    )
    assert len(all_hits) == 2
    only_active = await store.search_companies_by_name(
        "ПАО", limit=10, only_active=True
    )
    assert len(only_active) == 1
    assert only_active[0]["status"] == "active"


@pytest.mark.asyncio
async def test_fts_short_or_garbage_returns_empty(store: SQLiteStore) -> None:
    await store.upsert_company(make_company_row())
    # одиночный символ и спец-мусор отфильтруются
    assert await store.search_companies_by_name(
        "а", limit=10, only_active=False
    ) == []
    assert await store.search_companies_by_name(
        "!! ", limit=10, only_active=False
    ) == []


@pytest.mark.asyncio
async def test_fts_empty_db(store: SQLiteStore) -> None:
    hits = await store.search_companies_by_name(
        "нет_такой_компании", limit=10, only_active=False
    )
    assert hits == []


@pytest.mark.asyncio
async def test_count_reflects_inserts(store: SQLiteStore) -> None:
    await store.upsert_company(make_company_row())
    await store.upsert_ie(make_ie_row())
    counts = await store.count()
    assert counts["companies"] == 1
    assert counts["individual_entrepreneurs"] == 1
