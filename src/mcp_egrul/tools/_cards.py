"""Помощники для построения pydantic-карточек из строк SQLite.

Внутренний модуль (префикс `_`), используется только тулзами внутри
пакета `mcp_egrul.tools`.
"""

from __future__ import annotations

from datetime import date as DateT
from datetime import datetime
from typing import Any

from ..errors import McpEgrulError, NotFoundError
from ..schemas import (
    AddressStructured,
    CompanyCard,
    Director,
    Founder,
    IECard,
    OkvedEntry,
)


def parse_iso_date(value: Any, *, field_name: str) -> DateT:
    if isinstance(value, DateT) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return DateT.fromisoformat(value)
        except ValueError as exc:
            raise McpEgrulError(
                f"Невалидная дата в поле {field_name}: '{value}'.",
                details={"field": field_name, "value": value},
            ) from exc
    raise McpEgrulError(
        f"Не заполнено обязательное поле {field_name}.",
        details={"field": field_name},
    )


def parse_iso_date_optional(value: Any) -> DateT | None:
    if value is None or value == "":
        return None
    if isinstance(value, DateT) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return DateT.fromisoformat(value)
    return None


def parse_iso_datetime(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise McpEgrulError(
                f"Невалидный timestamp в поле {field_name}: '{value}'.",
                details={"field": field_name, "value": value},
            ) from exc
    raise McpEgrulError(
        f"Не заполнено обязательное поле {field_name}.",
        details={"field": field_name},
    )


def build_company_card(row: dict[str, Any] | None, *, identifier: str) -> CompanyCard:
    """Построить `CompanyCard` из строки таблицы `companies`.

    Параметр `identifier` — то, по чему искали (ИНН/ОГРН) — идёт в
    сообщение `NotFoundError` если строка пустая.
    """
    if row is None:
        raise NotFoundError(
            f"Компания не найдена в локальном слепке: {identifier}.",
            hint=(
                "Возможно, данные ещё не импортированы. "
                "Запустите `atomno-mcp-egrul-import` (Phase 1) или задайте "
                "ATOMNO_API_KEY для hosted-режима."
            ),
            details={"identifier": identifier},
        )

    data_json: dict[str, Any] = row.get("data_json") or {}

    okved_main = _okved_entry(
        code=row.get("okved_main_code"),
        description=row.get("okved_main_description"),
    )
    okved_additional = [
        OkvedEntry(**o) for o in _iter_dicts(data_json.get("okved_additional"))
    ]

    director_raw = data_json.get("director")
    director: Director | None = None
    if isinstance(director_raw, dict) and director_raw:
        director = Director.model_validate(director_raw)

    founders_raw = data_json.get("founders") or []
    founders = [Founder.model_validate(f) for f in _iter_dicts(founders_raw)]

    address_structured_raw = data_json.get("address_legal_structured")
    address_structured: AddressStructured | None = None
    if isinstance(address_structured_raw, dict) and address_structured_raw:
        address_structured = AddressStructured.model_validate(address_structured_raw)

    return CompanyCard(
        inn=row["inn"],
        ogrn=row["ogrn"],
        kpp=row.get("kpp"),
        okpo=row.get("okpo"),
        name_short=row["name_short"],
        name_full=row["name_full"],
        name_latin=row.get("name_latin"),
        status=row["status"],
        registered_at=parse_iso_date(row.get("registered_at"), field_name="registered_at"),
        liquidated_at=parse_iso_date_optional(row.get("liquidated_at")),
        address_legal=row.get("address_legal"),
        address_legal_structured=address_structured,
        okved_main=okved_main,
        okved_additional=okved_additional,
        director=director,
        founders=founders,
        authorized_capital=row.get("authorized_capital"),
        last_report_year=row.get("last_report_year"),
        source=row["source"],
        source_date=parse_iso_date(row.get("source_date"), field_name="source_date"),
        fetched_at=parse_iso_datetime(row.get("updated_at"), field_name="updated_at"),
    )


def build_ie_card(row: dict[str, Any] | None, *, identifier: str) -> IECard:
    if row is None:
        raise NotFoundError(
            f"Индивидуальный предприниматель не найден: {identifier}.",
            hint=(
                "Возможно, данные ещё не импортированы. "
                "Запустите `atomno-mcp-egrul-import --registry egrip` (Phase 1) или "
                "задайте ATOMNO_API_KEY для hosted-режима."
            ),
            details={"identifier": identifier},
        )

    data_json: dict[str, Any] = row.get("data_json") or {}

    okved_main = _okved_entry(
        code=row.get("okved_main_code"),
        description=row.get("okved_main_description"),
    )
    okved_additional = [
        OkvedEntry(**o) for o in _iter_dicts(data_json.get("okved_additional"))
    ]

    return IECard(
        ogrnip=row["ogrnip"],
        inn=row["inn"],
        fio=row["fio"],
        citizenship=row.get("citizenship"),
        status=row["status"],
        registered_at=parse_iso_date(row.get("registered_at"), field_name="registered_at"),
        closed_at=parse_iso_date_optional(row.get("closed_at")),
        okved_main=okved_main,
        okved_additional=okved_additional,
        source=row["source"],
        source_date=parse_iso_date(row.get("source_date"), field_name="source_date"),
        fetched_at=parse_iso_datetime(row.get("updated_at"), field_name="updated_at"),
    )


def _okved_entry(*, code: Any, description: Any) -> OkvedEntry | None:
    if not isinstance(code, str) or not code:
        return None
    desc = description if isinstance(description, str) and description else None
    return OkvedEntry(code=code, description=desc)


def _iter_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
