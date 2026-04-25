"""Интеграционный тест полного цикла `import → search → get_card`.

Сценарий:
    1. Копируем fixture-XML в `dumps/egrul/<date>/`.
    2. Запускаем `OpenDataSource.run_ingest(full=True)` через полноценный
       `ServiceContext`.
    3. Проверяем, что `search_by_inn`, `search_by_ogrn`, `search_by_name`,
       `bulk_cards`, `get_director`, `get_founders` возвращают корректные
       данные, парсерный skip записан в `import_log` как `errors_count > 0`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mcp_egrul.context import ServiceContext
from mcp_egrul.errors import NotFoundError
from mcp_egrul.sources.opendata import OpenDataSource
from mcp_egrul.tools.bulk_cards import bulk_cards
from mcp_egrul.tools.get_director import get_director
from mcp_egrul.tools.get_founders import get_founders
from mcp_egrul.tools.search_by_inn import search_by_inn
from mcp_egrul.tools.search_by_name import search_by_name
from mcp_egrul.tools.search_by_ogrn import search_by_ogrn

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def prepared_dumps_dir(tmp_path: Path) -> Path:
    dumps_dir = tmp_path / "dumps"
    for registry, name in (
        ("egrul", "egrul_sample.xml"),
        ("egrip", "egrip_sample.xml"),
    ):
        target = dumps_dir / registry / "2026-04-01"
        target.mkdir(parents=True)
        shutil.copy(FIXTURES / name, target / name)
    return dumps_dir


async def test_full_cycle_import_and_lookup(
    ctx: ServiceContext, prepared_dumps_dir: Path
) -> None:
    source = OpenDataSource(
        user_agent="mcp-egrul-test", http_timeout_seconds=5.0
    )
    report_egrul = await source.run_ingest(
        ctx.store,
        registry="egrul",
        dumps_dir=prepared_dumps_dir,
        full=True,
    )
    report_egrip = await source.run_ingest(
        ctx.store,
        registry="egrip",
        dumps_dir=prepared_dumps_dir,
        full=True,
    )
    assert report_egrul.records_imported == 2
    assert report_egrul.errors_count == 1
    assert report_egrip.records_imported == 2

    sber_card = await search_by_inn(ctx, "7707083893")
    assert sber_card.name_short == "ПАО СБЕРБАНК"
    assert sber_card.status == "active"
    assert sber_card.okved_main.code == "64.19"
    assert sber_card.director is not None
    assert sber_card.director.fio == "Греф Герман Оскарович"
    assert len(sber_card.founders) == 1
    assert sber_card.founders[0].type == "legal"

    gazprom_card = await search_by_ogrn(ctx, "1037700013020")
    assert gazprom_card.name_short == "ПАО ГАЗПРОМ"
    assert gazprom_card.director is not None
    assert "Миллер" in gazprom_card.director.fio

    hits = await search_by_name(ctx, "сбербанк", limit=5, only_active=True)
    assert len(hits) >= 1
    assert any(h.inn == "7707083893" for h in hits)

    bulk = await bulk_cards(ctx, inns=["7707083893", "7728168971", "0000000000"])
    assert len(bulk.cards) == 2
    assert len(bulk.errors) == 1
    assert bulk.errors[0].inn == "0000000000"

    director = await get_director(ctx, "7707083893")
    assert director is not None
    assert director.fio == "Греф Герман Оскарович"

    founders = await get_founders(ctx, "7707083893")
    assert len(founders) == 1
    assert "Центральный банк" in founders[0].name

    ivanov = await search_by_inn(ctx, "500100732259")
    assert ivanov.fio == "Иванов Иван Иванович"
    assert ivanov.status == "active"

    with pytest.raises(NotFoundError):
        await search_by_inn(ctx, "7704217370")
