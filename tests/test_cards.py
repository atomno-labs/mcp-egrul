"""Тесты хелперов `mcp_egrul.tools._cards`.

Проверяем все edge-case'ы парсинга дат/дат-времён и строительства карточек
из скудных SQL-строк. `no silent fallback` — битые значения всегда
бросают `McpEgrulError`, а не возвращают `None`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from mcp_egrul.errors import McpEgrulError, NotFoundError
from mcp_egrul.tools._cards import (
    _iter_dicts,
    _okved_entry,
    build_company_card,
    build_ie_card,
    parse_iso_date,
    parse_iso_date_optional,
    parse_iso_datetime,
)

# ---------------------------------------------------------------------------
# parse_iso_date
# ---------------------------------------------------------------------------


def test_parse_iso_date_accepts_date_object() -> None:
    d = date(2026, 4, 24)
    assert parse_iso_date(d, field_name="registered_at") == d


def test_parse_iso_date_accepts_iso_string() -> None:
    assert parse_iso_date("1991-03-20", field_name="registered_at") == date(
        1991, 3, 20
    )


def test_parse_iso_date_rejects_invalid_string() -> None:
    with pytest.raises(McpEgrulError) as exc_info:
        parse_iso_date("20/03/1991", field_name="registered_at")
    assert exc_info.value.details["field"] == "registered_at"
    assert exc_info.value.details["value"] == "20/03/1991"


def test_parse_iso_date_rejects_none_or_empty() -> None:
    with pytest.raises(McpEgrulError):
        parse_iso_date(None, field_name="registered_at")
    with pytest.raises(McpEgrulError):
        parse_iso_date("", field_name="registered_at")


def test_parse_iso_date_rejects_non_string_non_date() -> None:
    with pytest.raises(McpEgrulError):
        parse_iso_date(12345, field_name="x")


# ---------------------------------------------------------------------------
# parse_iso_date_optional
# ---------------------------------------------------------------------------


def test_parse_iso_date_optional_none_or_empty() -> None:
    assert parse_iso_date_optional(None) is None
    assert parse_iso_date_optional("") is None


def test_parse_iso_date_optional_accepts_date() -> None:
    d = date(2020, 12, 31)
    assert parse_iso_date_optional(d) == d


def test_parse_iso_date_optional_accepts_string() -> None:
    assert parse_iso_date_optional("2020-12-31") == date(2020, 12, 31)


def test_parse_iso_date_optional_non_string_non_date_returns_none() -> None:
    # По контракту optional-версия мягкая: непонятный тип → None.
    assert parse_iso_date_optional(42) is None


# ---------------------------------------------------------------------------
# parse_iso_datetime
# ---------------------------------------------------------------------------


def test_parse_iso_datetime_accepts_datetime_object() -> None:
    now = datetime.now(tz=UTC)
    assert parse_iso_datetime(now, field_name="updated_at") == now


def test_parse_iso_datetime_accepts_iso_string() -> None:
    assert parse_iso_datetime(
        "2026-04-24T18:30:00+00:00", field_name="updated_at"
    ) == datetime(2026, 4, 24, 18, 30, 0, tzinfo=UTC)


def test_parse_iso_datetime_rejects_invalid_string() -> None:
    with pytest.raises(McpEgrulError) as exc_info:
        parse_iso_datetime("not-a-timestamp", field_name="updated_at")
    assert exc_info.value.details["field"] == "updated_at"


def test_parse_iso_datetime_rejects_none_or_empty() -> None:
    with pytest.raises(McpEgrulError):
        parse_iso_datetime(None, field_name="updated_at")
    with pytest.raises(McpEgrulError):
        parse_iso_datetime("", field_name="updated_at")


# ---------------------------------------------------------------------------
# _okved_entry
# ---------------------------------------------------------------------------


def test_okved_entry_returns_none_on_missing_code() -> None:
    assert _okved_entry(code=None, description="что-то") is None
    assert _okved_entry(code="", description="что-то") is None
    assert _okved_entry(code=123, description="что-то") is None


def test_okved_entry_drops_non_string_description() -> None:
    entry = _okved_entry(code="64.19", description=123)
    assert entry is not None
    assert entry.code == "64.19"
    assert entry.description is None


# ---------------------------------------------------------------------------
# _iter_dicts
# ---------------------------------------------------------------------------


def test_iter_dicts_filters_out_non_dicts() -> None:
    value: Any = [{"a": 1}, "skip", None, {"b": 2}, 5]
    assert _iter_dicts(value) == [{"a": 1}, {"b": 2}]


def test_iter_dicts_returns_empty_on_non_list() -> None:
    assert _iter_dicts(None) == []
    assert _iter_dicts("string") == []
    assert _iter_dicts({"key": "value"}) == []


# ---------------------------------------------------------------------------
# build_company_card
# ---------------------------------------------------------------------------


def test_build_company_card_none_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        build_company_card(None, identifier="7707083893")


def test_build_company_card_minimal_row_no_director_no_address_struct() -> None:
    row: dict[str, Any] = {
        "inn": "7707083893",
        "ogrn": "1027700132195",
        "kpp": None,
        "okpo": None,
        "name_short": "X",
        "name_full": "Полное X",
        "name_latin": None,
        "status": "active",
        "registered_at": date(2001, 1, 1).isoformat(),
        "liquidated_at": None,
        "address_legal": None,
        "okved_main_code": None,
        "okved_main_description": None,
        "authorized_capital": None,
        "last_report_year": None,
        "source": "opendata",
        "source_date": date(2026, 4, 1).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "data_json": {
            # Пустые/некорректные типы — должны быть мягко отброшены.
            "director": {},
            "founders": None,
            "address_legal_structured": None,
            "okved_additional": "not-a-list",
        },
    }
    card = build_company_card(row, identifier="7707083893")
    assert card.director is None
    assert card.founders == []
    assert card.address_legal_structured is None
    assert card.okved_main is None
    assert card.okved_additional == []


def test_build_company_card_nonempty_director_and_address_struct() -> None:
    row: dict[str, Any] = {
        "inn": "7707083893",
        "ogrn": "1027700132195",
        "kpp": "773601001",
        "okpo": None,
        "name_short": "X",
        "name_full": "Полное X",
        "name_latin": None,
        "status": "active",
        "registered_at": date(2001, 1, 1).isoformat(),
        "liquidated_at": None,
        "address_legal": "г. Москва, ул. Вавилова, 19",
        "okved_main_code": "64.19",
        "okved_main_description": "Денежное посредничество",
        "authorized_capital": 1000.0,
        "last_report_year": 2024,
        "source": "opendata",
        "source_date": date(2026, 4, 1).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "data_json": {
            "director": {"fio": "Иванов И. И.", "position": "Директор"},
            "founders": [],
            "address_legal_structured": {
                "postal_code": "117997",
                "region": "г. Москва",
                "city": None,
                "street": "ул. Вавилова",
                "house": "19",
            },
        },
    }
    card = build_company_card(row, identifier="7707083893")
    assert card.director is not None
    assert card.director.fio == "Иванов И. И."
    assert card.address_legal_structured is not None
    assert card.address_legal_structured.postal_code == "117997"


# ---------------------------------------------------------------------------
# build_ie_card
# ---------------------------------------------------------------------------


def test_build_ie_card_none_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        build_ie_card(None, identifier="500100732259")


def test_build_ie_card_minimal_row() -> None:
    row: dict[str, Any] = {
        "ogrnip": "304500116000061",
        "inn": "500100732259",
        "fio": "Иванов Иван Иванович",
        "citizenship": None,
        "status": "closed",
        "registered_at": date(2004, 1, 15).isoformat(),
        "closed_at": date(2020, 12, 31).isoformat(),
        "okved_main_code": None,
        "okved_main_description": None,
        "source": "opendata",
        "source_date": date(2026, 4, 1).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "data_json": {},
    }
    card = build_ie_card(row, identifier="500100732259")
    assert card.status == "closed"
    assert card.closed_at == date(2020, 12, 31)
    assert card.okved_main is None
    assert card.okved_additional == []
