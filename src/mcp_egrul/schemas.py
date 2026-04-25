"""Pydantic v2 модели публичного контракта mcp-egrul.

Соответствуют SPEC §4.2 (payload карточки) один-в-один. Все `Literal`-типы
опираются на `constants.COMPANY_STATUSES` / `IE_STATUSES` / `DATA_SOURCES` /
`FOUNDER_TYPES` — исходники enum-значений собраны в `constants.py`,
схемы здесь лишь переводят их в `Literal[...]`, чтобы IDE/валидатор видел
фиксированный набор.
"""

from __future__ import annotations

from datetime import date as DateT
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CompanyStatus = Literal[
    "active",
    "reorganizing",
    "liquidating",
    "liquidated",
    "bankrupt",
]

IEStatus = Literal[
    "active",
    "closed",
]

DataSource = Literal[
    "opendata",
    "egrul-scrape",
    "dadata",
    "hosted",
]

FounderType = Literal["person", "legal"]

SubjectKind = Literal["company", "ie"]


class StrictModel(BaseModel):
    """Базовая pydantic-модель: trimming строк, строгое игнорирование лишнего."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        populate_by_name=True,
        extra="ignore",
    )


# ---------------------------------------------------------------------------
# Подструктуры.
# ---------------------------------------------------------------------------


class OkvedEntry(StrictModel):
    code: str = Field(description="Код ОКВЭД (например '47.91.2').")
    description: str | None = Field(default=None, description="Расшифровка кода.")


class AddressStructured(StrictModel):
    postal_code: str | None = None
    region: str | None = None
    city: str | None = None
    street: str | None = None
    house: str | None = None


class Director(StrictModel):
    fio: str
    position: str = Field(
        description="Должность (например 'Генеральный директор', 'Директор')."
    )
    inn: str | None = Field(
        default=None,
        description="ИНН физлица-руководителя, если раскрыт в реестре.",
    )
    since: DateT | None = Field(
        default=None,
        description="Дата назначения, если известна.",
    )


class Founder(StrictModel):
    type: FounderType
    name: str = Field(description="ФИО или название юрлица-учредителя.")
    inn: str | None = None
    share_percent: float = Field(
        ge=0.0,
        le=100.0,
        description="Доля в уставном капитале, 0..100%.",
    )
    share_sum: float | None = Field(
        default=None,
        description="Номинальная стоимость доли в рублях (если известно).",
    )


# ---------------------------------------------------------------------------
# Карточки.
# ---------------------------------------------------------------------------


class CompanyCard(StrictModel):
    """Полная карточка юридического лица. SPEC §4.2."""

    inn: str
    ogrn: str
    kpp: str | None = None
    okpo: str | None = None

    name_short: str
    name_full: str
    name_latin: str | None = None

    status: CompanyStatus
    registered_at: DateT
    liquidated_at: DateT | None = None

    address_legal: str | None = None
    address_legal_structured: AddressStructured | None = None

    okved_main: OkvedEntry | None = None
    okved_additional: list[OkvedEntry] = Field(default_factory=list)

    director: Director | None = None
    founders: list[Founder] = Field(default_factory=list)

    authorized_capital: float | None = Field(
        default=None,
        description="Уставной капитал в рублях.",
    )
    last_report_year: int | None = None

    source: DataSource
    source_date: DateT = Field(
        description="Дата, на которую актуальны данные из источника.",
    )
    fetched_at: datetime = Field(
        description="Временная метка (UTC), когда сервер собрал эту карточку.",
    )


class IECard(StrictModel):
    """Полная карточка индивидуального предпринимателя. SPEC §4.2."""

    ogrnip: str
    inn: str
    fio: str
    citizenship: Literal["RU", "other"] | None = None

    status: IEStatus
    registered_at: DateT
    closed_at: DateT | None = None

    okved_main: OkvedEntry | None = None
    okved_additional: list[OkvedEntry] = Field(default_factory=list)

    source: DataSource
    source_date: DateT
    fetched_at: datetime


# ---------------------------------------------------------------------------
# Поиск.
# ---------------------------------------------------------------------------


class SearchHit(StrictModel):
    """Элемент ответа `search_by_name` (SPEC §4.2)."""

    kind: SubjectKind
    inn: str
    ogrn: str = Field(description="ОГРН для компании или ОГРНИП для ИП.")
    name: str
    status: str
    address_legal: str | None = None
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Нормализованный FTS5-ранк (1.0 — точное совпадение, 0.0 — краевое).",
    )


# ---------------------------------------------------------------------------
# Bulk.
# ---------------------------------------------------------------------------


class BulkItemError(StrictModel):
    """Ошибка по конкретному ИНН в `bulk_cards`. SPEC §4.3."""

    inn: str
    code: str
    message: str


class BulkResult(StrictModel):
    """Ответ `bulk_cards`: массив карточек + массив ошибок для не-нашедшихся."""

    cards: list[CompanyCard | IECard] = Field(default_factory=list)
    errors: list[BulkItemError] = Field(default_factory=list)
    requested: int
    found: int
