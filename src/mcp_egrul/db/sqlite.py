"""Async SQLite store для mcp-egrul (aiosqlite поверх схемы SPEC §7.1).

Назначение:
    Единственная точка, которая пишет и читает локальный SQLite-слепок
    ЕГРЮЛ/ЕГРИП. Все тулзы (`tools/*.py`) ходят только сюда.

DDL:
    Встроен в константу `SCHEMA_SQL` ниже. Ровно один источник правды —
    `_schema.md` в SPEC + эта строка. Любое изменение схемы = bump
    `SCHEMA_VERSION` + миграция (в Phase 0 миграций нет, только `init`).

Ошибки:
    * Любая ошибка SQLite — поднимается наружу как `McpEgrulError` (через
      `_wrap` ниже), чтобы тул-слой не ловил `aiosqlite.Error` напрямую.
    * Поиск по пустой БД возвращает пустой список, но НЕ подменяет это
      фиктивными данными (no silent fallback).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import UTC, datetime
from datetime import date as DateT
from pathlib import Path
from typing import Any

import aiosqlite

from ..constants import (
    TABLE_COMPANIES,
    TABLE_COMPANIES_FTS,
    TABLE_IE,
    TABLE_IMPORT_LOG,
)
from ..errors import McpEgrulError

SCHEMA_VERSION: int = 1

SCHEMA_SQL: str = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS companies (
    inn                    TEXT PRIMARY KEY
                              CHECK(length(inn) = 10),
    ogrn                   TEXT NOT NULL UNIQUE
                              CHECK(length(ogrn) = 13),
    kpp                    TEXT
                              CHECK(kpp IS NULL OR length(kpp) = 9),
    okpo                   TEXT,

    name_short             TEXT NOT NULL,
    name_full              TEXT NOT NULL,
    name_latin             TEXT,

    status                 TEXT NOT NULL
                              CHECK(status IN ('active','reorganizing','liquidating','liquidated','bankrupt')),
    registered_at          TEXT NOT NULL,
    liquidated_at          TEXT,

    address_legal          TEXT,

    okved_main_code        TEXT,
    okved_main_description TEXT,

    authorized_capital     REAL,
    last_report_year       INTEGER,

    source                 TEXT NOT NULL
                              CHECK(source IN ('opendata','egrul-scrape','dadata','hosted')),
    source_date            TEXT NOT NULL,
    updated_at             TEXT NOT NULL,

    data_json              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_companies_ogrn   ON companies(ogrn);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_okved  ON companies(okved_main_code);

CREATE TABLE IF NOT EXISTS individual_entrepreneurs (
    ogrnip                 TEXT PRIMARY KEY
                              CHECK(length(ogrnip) = 15),
    inn                    TEXT NOT NULL UNIQUE
                              CHECK(length(inn) = 12),

    fio                    TEXT NOT NULL,
    citizenship            TEXT
                              CHECK(citizenship IS NULL OR citizenship IN ('RU','other')),

    status                 TEXT NOT NULL
                              CHECK(status IN ('active','closed')),
    registered_at          TEXT NOT NULL,
    closed_at              TEXT,

    okved_main_code        TEXT,
    okved_main_description TEXT,

    source                 TEXT NOT NULL
                              CHECK(source IN ('opendata','egrul-scrape','dadata','hosted')),
    source_date            TEXT NOT NULL,
    updated_at             TEXT NOT NULL,

    data_json              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ie_inn    ON individual_entrepreneurs(inn);
CREATE INDEX IF NOT EXISTS idx_ie_status ON individual_entrepreneurs(status);

CREATE VIRTUAL TABLE IF NOT EXISTS companies_fts USING fts5(
    inn UNINDEXED,
    name_short,
    name_full,
    address_legal,
    content = 'companies',
    content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS ie_fts USING fts5(
    ogrnip UNINDEXED,
    fio,
    content = 'individual_entrepreneurs',
    content_rowid = 'rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS companies_ai AFTER INSERT ON companies BEGIN
    INSERT INTO companies_fts(rowid, inn, name_short, name_full, address_legal)
    VALUES (new.rowid, new.inn, new.name_short, new.name_full, new.address_legal);
END;

CREATE TRIGGER IF NOT EXISTS companies_ad AFTER DELETE ON companies BEGIN
    INSERT INTO companies_fts(companies_fts, rowid, inn, name_short, name_full, address_legal)
    VALUES ('delete', old.rowid, old.inn, old.name_short, old.name_full, old.address_legal);
END;

CREATE TRIGGER IF NOT EXISTS companies_au AFTER UPDATE ON companies BEGIN
    INSERT INTO companies_fts(companies_fts, rowid, inn, name_short, name_full, address_legal)
    VALUES ('delete', old.rowid, old.inn, old.name_short, old.name_full, old.address_legal);
    INSERT INTO companies_fts(rowid, inn, name_short, name_full, address_legal)
    VALUES (new.rowid, new.inn, new.name_short, new.name_full, new.address_legal);
END;

CREATE TRIGGER IF NOT EXISTS ie_ai AFTER INSERT ON individual_entrepreneurs BEGIN
    INSERT INTO ie_fts(rowid, ogrnip, fio) VALUES (new.rowid, new.ogrnip, new.fio);
END;

CREATE TRIGGER IF NOT EXISTS ie_ad AFTER DELETE ON individual_entrepreneurs BEGIN
    INSERT INTO ie_fts(ie_fts, rowid, ogrnip, fio)
    VALUES ('delete', old.rowid, old.ogrnip, old.fio);
END;

CREATE TRIGGER IF NOT EXISTS ie_au AFTER UPDATE ON individual_entrepreneurs BEGIN
    INSERT INTO ie_fts(ie_fts, rowid, ogrnip, fio)
    VALUES ('delete', old.rowid, old.ogrnip, old.fio);
    INSERT INTO ie_fts(rowid, ogrnip, fio) VALUES (new.rowid, new.ogrnip, new.fio);
END;

CREATE TABLE IF NOT EXISTS import_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    source_dump_date     TEXT NOT NULL,
    records_imported     INTEGER,
    records_updated      INTEGER,
    errors_count         INTEGER,
    status               TEXT
                            CHECK(status IN ('running','success','failed'))
);

CREATE TABLE IF NOT EXISTS _schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _iso_now_utc() -> str:
    return datetime.now(tz=UTC).isoformat()


def _wrap(action: str):
    """Декоратор для превращения aiosqlite-ошибок в McpEgrulError."""

    def decorator(func):
        async def wrapper(*args: Any, **kwargs: Any):
            try:
                return await func(*args, **kwargs)
            except aiosqlite.Error as exc:
                raise McpEgrulError(
                    f"Ошибка SQLite при {action}: {exc}",
                    details={"action": action, "driver_error": str(exc)},
                ) from exc

        wrapper.__name__ = func.__name__
        return wrapper

    return decorator


class SQLiteStore:
    """Async-клиент локального слепка ЕГРЮЛ/ЕГРИП.

    Использование:
        store = SQLiteStore(Path("./mcp_egrul_data.sqlite"))
        await store.init()
        await store.upsert_company(company_row)
        record = await store.get_company_by_inn("7707083893")

    Одна инстанция на процесс; коннект открывается на каждый запрос
    (aiosqlite короткоживущими коннектами, как в парном mcp-fns-check).
    Это безопасно для SQLite и даёт чистую семантику ошибок.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._initialised = False

    # --- lifecycle -----------------------------------------------------

    @_wrap("инициализации БД")
    async def init(self) -> None:
        """Создать схему, если её нет. Идемпотентно."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.execute(
                "INSERT OR IGNORE INTO _schema_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            await db.commit()
        self._initialised = True

    async def _ensure(self) -> None:
        if not self._initialised:
            await self.init()

    async def close(self) -> None:  # pragma: no cover - точка расширения
        """Зарезервировано под pool/persistent-коннекты."""
        self._initialised = False

    # --- companies -----------------------------------------------------

    @_wrap("чтения карточки юр.лица по ИНН")
    async def get_company_by_inn(self, inn: str) -> dict[str, Any] | None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM {TABLE_COMPANIES} WHERE inn = ?",
                (inn,),
            ) as cur:
                row = await cur.fetchone()
                return _row_to_dict(row)

    @_wrap("чтения карточки юр.лица по ОГРН")
    async def get_company_by_ogrn(self, ogrn: str) -> dict[str, Any] | None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM {TABLE_COMPANIES} WHERE ogrn = ?",
                (ogrn,),
            ) as cur:
                row = await cur.fetchone()
                return _row_to_dict(row)

    @_wrap("записи карточки юр.лица")
    async def upsert_company(self, company: dict[str, Any]) -> None:
        """Вставить/обновить запись юр.лица.

        Словарь должен содержать все NOT NULL-поля схемы. Поле `data_json`
        можно передать в виде dict (будет сериализовано) или строки.
        """
        await self._ensure()
        row = _prepare_company_row(company)
        columns = ",".join(row.keys())
        placeholders = ",".join("?" * len(row))
        updates = ",".join(f"{k} = excluded.{k}" for k in row.keys() if k != "inn")
        sql = (
            f"INSERT INTO {TABLE_COMPANIES} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(inn) DO UPDATE SET {updates}"
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(sql, tuple(row.values()))
            await db.commit()

    # --- individual entrepreneurs --------------------------------------

    @_wrap("чтения карточки ИП по ИНН")
    async def get_ie_by_inn(self, inn: str) -> dict[str, Any] | None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM {TABLE_IE} WHERE inn = ?",
                (inn,),
            ) as cur:
                row = await cur.fetchone()
                return _row_to_dict(row)

    @_wrap("чтения карточки ИП по ОГРНИП")
    async def get_ie_by_ogrnip(self, ogrnip: str) -> dict[str, Any] | None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM {TABLE_IE} WHERE ogrnip = ?",
                (ogrnip,),
            ) as cur:
                row = await cur.fetchone()
                return _row_to_dict(row)

    @_wrap("записи карточки ИП")
    async def upsert_ie(self, ie: dict[str, Any]) -> None:
        await self._ensure()
        row = _prepare_ie_row(ie)
        columns = ",".join(row.keys())
        placeholders = ",".join("?" * len(row))
        updates = ",".join(f"{k} = excluded.{k}" for k in row.keys() if k != "ogrnip")
        sql = (
            f"INSERT INTO {TABLE_IE} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(ogrnip) DO UPDATE SET {updates}"
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(sql, tuple(row.values()))
            await db.commit()

    # --- FTS5 search ---------------------------------------------------

    @_wrap("FTS5-поиска по названию")
    async def search_companies_by_name(
        self,
        query: str,
        *,
        limit: int,
        only_active: bool,
    ) -> list[dict[str, Any]]:
        """Fuzzy-поиск по `companies_fts`.

        Возвращает список строк с нормализованным `relevance_score` ∈ [0.0, 1.0].
        FTS5 `bm25()` даёт неотрицательное число «меньше = лучше»; мы превращаем
        его в score через 1/(1+bm25).
        """
        await self._ensure()
        fts_query = _to_fts_prefix_query(query)
        if fts_query is None:
            return []

        status_filter = " AND c.status = 'active'" if only_active else ""
        sql = f"""
            SELECT
                c.inn, c.ogrn, c.name_short, c.name_full,
                c.status, c.address_legal,
                bm25({TABLE_COMPANIES_FTS}) AS rank
            FROM {TABLE_COMPANIES_FTS} fts
            JOIN {TABLE_COMPANIES} c ON c.rowid = fts.rowid
            WHERE {TABLE_COMPANIES_FTS} MATCH ?
            {status_filter}
            ORDER BY rank ASC
            LIMIT ?
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, (fts_query, limit)) as cur:
                rows = await cur.fetchall()

        hits: list[dict[str, Any]] = []
        for r in rows:
            rank = float(r["rank"]) if r["rank"] is not None else math.inf
            hits.append(
                {
                    "kind": "company",
                    "inn": r["inn"],
                    "ogrn": r["ogrn"],
                    "name": r["name_short"] or r["name_full"],
                    "status": r["status"],
                    "address_legal": r["address_legal"],
                    "relevance_score": _normalize_bm25(rank),
                }
            )
        return hits

    # --- import log ----------------------------------------------------

    @_wrap("записи старта импорта")
    async def start_import(
        self, *, source_dump_date: str, started_at: str
    ) -> int:
        """Добавить строку в `import_log` со статусом `running` и вернуть её id."""
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                f"INSERT INTO {TABLE_IMPORT_LOG} "
                "(started_at, source_dump_date, records_imported, "
                "records_updated, errors_count, status) "
                "VALUES (?, ?, 0, 0, 0, 'running')",
                (started_at, source_dump_date),
            ) as cur:
                row_id = cur.lastrowid
            await db.commit()
        if row_id is None:
            raise McpEgrulError(
                "Не удалось получить id строки import_log.",
                details={"source_dump_date": source_dump_date},
            )
        return int(row_id)

    @_wrap("записи финала импорта")
    async def finish_import(
        self,
        import_id: int,
        *,
        finished_at: str,
        records_imported: int,
        records_updated: int,
        errors_count: int,
        status: str,
    ) -> None:
        """Закрыть строку импорта (`success` | `failed`)."""
        if status not in ("success", "failed"):
            raise McpEgrulError(
                f"Недопустимый финальный статус импорта: {status!r}.",
                details={"status": status},
            )
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE {TABLE_IMPORT_LOG} SET "
                "finished_at = ?, records_imported = ?, records_updated = ?, "
                "errors_count = ?, status = ? WHERE id = ?",
                (
                    finished_at,
                    records_imported,
                    records_updated,
                    errors_count,
                    status,
                    import_id,
                ),
            )
            await db.commit()

    @_wrap("чтения последнего успешного импорта")
    async def last_successful_import_date(self) -> str | None:
        """Вернуть `source_dump_date` последнего успешного импорта (ISO) или None."""
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                f"SELECT source_dump_date FROM {TABLE_IMPORT_LOG} "
                "WHERE status = 'success' "
                "ORDER BY finished_at DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
                if row is None:
                    return None
                return str(row[0])

    # --- housekeeping --------------------------------------------------

    @_wrap("подсчёта записей")
    async def count(self) -> dict[str, int]:
        """Вернуть число строк в основных таблицах (для `ping` и мониторинга)."""
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            counts: dict[str, int] = {}
            for table in (TABLE_COMPANIES, TABLE_IE, TABLE_IMPORT_LOG):
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                    row = await cur.fetchone()
                    counts[table] = int(row[0]) if row else 0
            return counts


# ---------------------------------------------------------------------------
# Низкоуровневые хелперы.
# ---------------------------------------------------------------------------


_COMPANY_COLUMNS: tuple[str, ...] = (
    "inn",
    "ogrn",
    "kpp",
    "okpo",
    "name_short",
    "name_full",
    "name_latin",
    "status",
    "registered_at",
    "liquidated_at",
    "address_legal",
    "okved_main_code",
    "okved_main_description",
    "authorized_capital",
    "last_report_year",
    "source",
    "source_date",
    "updated_at",
    "data_json",
)

_IE_COLUMNS: tuple[str, ...] = (
    "ogrnip",
    "inn",
    "fio",
    "citizenship",
    "status",
    "registered_at",
    "closed_at",
    "okved_main_code",
    "okved_main_description",
    "source",
    "source_date",
    "updated_at",
    "data_json",
)


def _prepare_company_row(company: dict[str, Any]) -> dict[str, Any]:
    return _prepare_row(company, _COMPANY_COLUMNS)


def _prepare_ie_row(ie: dict[str, Any]) -> dict[str, Any]:
    return _prepare_row(ie, _IE_COLUMNS)


def _prepare_row(src: dict[str, Any], columns: Iterable[str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for col in columns:
        value = src.get(col)
        if col == "data_json":
            if value is None:
                raise McpEgrulError(
                    "Не задан data_json при записи в SQLite.",
                    details={"missing_column": col},
                )
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
        elif col == "updated_at" and value is None:
            value = _iso_now_utc()
        elif isinstance(value, DateT) and not isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, datetime):
            value = value.isoformat()
        row[col] = value
    return row


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result: dict[str, Any] = {k: row[k] for k in row.keys()}
    raw = result.get("data_json")
    if isinstance(raw, str) and raw:
        try:
            result["data_json"] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise McpEgrulError(
                "Повреждён data_json в SQLite.",
                details={"json_error": str(exc)},
            ) from exc
    return result


def _to_fts_prefix_query(user_query: str) -> str | None:
    """Превратить пользовательский запрос в безопасный FTS5 prefix-query.

    FTS5 воспринимает `*` как префикс-вайлдкард. Мы:
        1. Разбиваем по пробелам.
        2. Оставляем только alnum-и-подчёркивание-токены длиной ≥ 2.
        3. Добавляем `*` в конец каждого токена.
        4. Склеиваем через `AND`.
    Это исключает инъекцию спец-символов FTS5 (`"`, `(`, `:`) и не маскирует
    невалидный вход пустым результатом молча — `None` означает «запрос
    нечего искать», вызывающая сторона отдаст пустой список явно.
    """
    tokens = [t for t in user_query.replace('"', " ").split() if t]
    cleaned = [
        "".join(ch for ch in tok if ch.isalnum() or ch == "_") for tok in tokens
    ]
    filtered = [c for c in cleaned if len(c) >= 2]
    if not filtered:
        return None
    return " AND ".join(f"{tok}*" for tok in filtered)


def _normalize_bm25(rank: float) -> float:
    """bm25 → [0..1] score. Чем меньше bm25, тем лучше совпадение."""
    if rank == math.inf:
        return 0.0
    if rank <= 0:
        return 1.0
    return round(1.0 / (1.0 + rank), 4)
