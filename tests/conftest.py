"""Общие фикстуры тестов mcp-egrul."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from mcp_egrul.config import Config
from mcp_egrul.context import ServiceContext
from mcp_egrul.db import SQLiteStore


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_mcp_egrul.sqlite"


@pytest_asyncio.fixture
async def store(tmp_db_path: Path) -> SQLiteStore:
    s = SQLiteStore(tmp_db_path)
    await s.init()
    return s


@pytest.fixture
def config(tmp_db_path: Path, tmp_path: Path) -> Config:
    return Config(
        db_path=tmp_db_path,
        dumps_dir=tmp_path / "dumps",
        user_agent="mcp-egrul-test/0.1",
        http_timeout_seconds=5.0,
        log_level="INFO",
        hosted_api_key=None,
        hosted_api_base="https://api.atomno.ru/mcp-egrul/v1",
    )


@pytest_asyncio.fixture
async def ctx(store: SQLiteStore, config: Config) -> ServiceContext:
    c = ServiceContext.for_testing(store=store, config=config)
    await c.__aenter__()
    try:
        yield c
    finally:
        await c.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Справочные валидные идентификаторы.
# Контрольные цифры посчитаны и сходятся — это не random-строки, а реальные
# публично известные ИНН / ОГРН крупных компаний РФ.
# ---------------------------------------------------------------------------

VALID_INN_LEGAL: list[str] = [
    "7707083893",   # Сбербанк
    "7728168971",   # Газпром
    "7704217370",   # Роснефть
]

VALID_INN_IE: list[str] = [
    "500100732259",
    "773173381311",
]

VALID_OGRN_LEGAL: list[str] = [
    "1027700132195",   # Сбербанк
    "1037700013020",   # Газпром
]

VALID_OGRNIP: list[str] = [
    "304500116000061",
    "320774000000048",
]


def make_company_row(**overrides: Any) -> dict[str, Any]:
    """Фабрика строки таблицы companies для тестов."""
    base: dict[str, Any] = {
        "inn": "7707083893",
        "ogrn": "1027700132195",
        "kpp": "773601001",
        "okpo": None,
        "name_short": "ПАО СБЕРБАНК",
        "name_full": (
            "ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО 'СБЕРБАНК РОССИИ'"
        ),
        "name_latin": None,
        "status": "active",
        "registered_at": date(1991, 3, 20).isoformat(),
        "liquidated_at": None,
        "address_legal": "117997, г. Москва, ул. Вавилова, 19",
        "okved_main_code": "64.19",
        "okved_main_description": "Денежное посредничество прочее",
        "authorized_capital": 67760844000.0,
        "last_report_year": 2024,
        "source": "opendata",
        "source_date": date(2026, 4, 1).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "data_json": {
            "okved_additional": [
                {"code": "64.99.1", "description": "Вложения в ценные бумаги"}
            ],
            "director": {
                "fio": "Греф Герман Оскарович",
                "position": "Президент, Председатель Правления",
            },
            "founders": [
                {
                    "type": "legal",
                    "name": "Центральный банк Российской Федерации",
                    "share_percent": 50.0,
                },
            ],
        },
    }
    base.update(overrides)
    return base


def make_ie_row(**overrides: Any) -> dict[str, Any]:
    """Фабрика строки таблицы individual_entrepreneurs для тестов."""
    base: dict[str, Any] = {
        "ogrnip": "304500116000061",
        "inn": "500100732259",
        "fio": "Иванов Иван Иванович",
        "citizenship": "RU",
        "status": "active",
        "registered_at": date(2004, 1, 15).isoformat(),
        "closed_at": None,
        "okved_main_code": "47.91.2",
        "okved_main_description": (
            "Торговля розничная, осуществляемая непосредственно "
            "при помощи информационно-коммуникационной сети Интернет"
        ),
        "source": "opendata",
        "source_date": date(2026, 4, 1).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "data_json": {
            "okved_additional": [],
        },
    }
    base.update(overrides)
    return base
