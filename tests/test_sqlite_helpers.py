"""Тесты приватных хелперов `db/sqlite.py`.

Покрывают:
    * `_iso_now_utc` — формат и таймзона;
    * `_wrap` — перехват `aiosqlite.Error` → `McpEgrulError`;
    * `_prepare_row` — обязательный `data_json`, авто-заполнение `updated_at`,
      сериализация date/datetime;
    * `_row_to_dict` — JSONDecodeError на битый `data_json`;
    * `_normalize_bm25` — граничные значения (inf, 0, положительный);
    * `finish_import` с невалидным статусом.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from pathlib import Path

import aiosqlite
import pytest

from mcp_egrul.db.sqlite import (
    SQLiteStore,
    _iso_now_utc,
    _normalize_bm25,
    _prepare_row,
    _row_to_dict,
    _wrap,
)
from mcp_egrul.errors import McpEgrulError

# ---------------------------------------------------------------------------
# _iso_now_utc
# ---------------------------------------------------------------------------


def test_iso_now_utc_returns_iso_string_with_utc_offset() -> None:
    iso = _iso_now_utc()
    assert isinstance(iso, str)
    assert iso.endswith("+00:00")
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# _wrap декоратор — перехват SQLite-ошибок.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrap_converts_aiosqlite_error_to_mcp_egrul_error() -> None:
    @_wrap("тестовой операции")
    async def broken() -> None:
        raise aiosqlite.Error("simulated SQLite failure")

    with pytest.raises(McpEgrulError) as info:
        await broken()
    err = info.value
    assert "SQLite при тестовой операции" in err.message_ru
    assert err.details["action"] == "тестовой операции"
    assert err.details["driver_error"] == "simulated SQLite failure"


@pytest.mark.asyncio
async def test_wrap_passes_through_return_value() -> None:
    @_wrap("тестовой операции")
    async def happy() -> int:
        return 42

    assert await happy() == 42


# ---------------------------------------------------------------------------
# _prepare_row
# ---------------------------------------------------------------------------


def test_prepare_row_missing_data_json_raises() -> None:
    with pytest.raises(McpEgrulError, match="data_json"):
        _prepare_row({"inn": "7707083893"}, ("inn", "data_json"))


def test_prepare_row_fills_updated_at_when_none() -> None:
    row = _prepare_row(
        {"updated_at": None, "data_json": {}},
        ("updated_at", "data_json"),
    )
    assert row["updated_at"] is not None
    parsed = datetime.fromisoformat(row["updated_at"])
    assert parsed.tzinfo is not None


def test_prepare_row_serialises_date_and_datetime() -> None:
    row = _prepare_row(
        {
            "registered_at": date(1991, 3, 20),
            "updated_at": datetime(2026, 4, 24, 18, 0, tzinfo=UTC),
            "data_json": {},
        },
        ("registered_at", "updated_at", "data_json"),
    )
    assert row["registered_at"] == "1991-03-20"
    assert row["updated_at"].startswith("2026-04-24T18:00:00")


def test_prepare_row_serialises_dict_data_json_to_string() -> None:
    row = _prepare_row(
        {"data_json": {"okved_additional": [{"code": "64.19"}]}},
        ("data_json",),
    )
    assert isinstance(row["data_json"], str)
    assert "64.19" in row["data_json"]


def test_prepare_row_preserves_string_data_json_unchanged() -> None:
    pre_serialised = '{"preserved":true}'
    row = _prepare_row({"data_json": pre_serialised}, ("data_json",))
    assert row["data_json"] == pre_serialised


# ---------------------------------------------------------------------------
# _row_to_dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_to_dict_raises_on_corrupt_data_json(tmp_path: Path) -> None:
    """Если в `data_json` лежит не-JSON строка — поднимаем `McpEgrulError`."""
    db_path = tmp_path / "corrupt.sqlite"
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE t (data_json TEXT)")
        await db.execute(
            "INSERT INTO t(data_json) VALUES (?)",
            ("{not-valid-json",),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM t") as cur:
            row = await cur.fetchone()

    with pytest.raises(McpEgrulError, match="Повреждён data_json"):
        _row_to_dict(row)


def test_row_to_dict_returns_none_for_none_input() -> None:
    assert _row_to_dict(None) is None


@pytest.mark.asyncio
async def test_row_to_dict_preserves_empty_string_data_json(tmp_path: Path) -> None:
    """Пустая строка в `data_json` — НЕ парсится (json.loads('') → JSONDecodeError).

    Покрывает ветку `isinstance(raw, str) and raw` → False на пустой строке:
    мы просто оставляем значение как есть и не бросаем исключение (пустая
    строка — допустимое legacy-состояние, битый JSON — нет).
    """
    db_path = tmp_path / "empty.sqlite"
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE t (data_json TEXT)")
        await db.execute("INSERT INTO t(data_json) VALUES (?)", ("",))
        await db.commit()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM t") as cur:
            row = await cur.fetchone()

    result = _row_to_dict(row)
    assert result is not None
    assert result["data_json"] == ""  # оставили как есть, не ошибка


@pytest.mark.asyncio
async def test_row_to_dict_preserves_non_string_data_json(tmp_path: Path) -> None:
    """Если в колонке лежит NULL → ветка `isinstance(raw, str)` → False."""
    db_path = tmp_path / "nullcol.sqlite"
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE t (data_json TEXT)")
        await db.execute("INSERT INTO t(data_json) VALUES (NULL)")
        await db.commit()
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM t") as cur:
            row = await cur.fetchone()

    result = _row_to_dict(row)
    assert result is not None
    assert result["data_json"] is None


@pytest.mark.asyncio
async def test_start_import_raises_when_lastrowid_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Крайний случай: `cur.lastrowid is None` после INSERT → защитный
    `McpEgrulError` вместо silent fallback. Это соответствует красной линии
    "запрет silent fallback" — мы не возвращаем 0 или -1, мы кричим.

    Воспроизводим через monkeypatch `aiosqlite.connect`, возвращая контекст,
    в котором cursor.lastrowid всегда None.
    """
    import mcp_egrul.db.sqlite as sqlite_module

    class _FakeCursor:
        lastrowid = None

        async def __aenter__(self) -> _FakeCursor:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    class _FakeDB:
        async def __aenter__(self) -> _FakeDB:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def execute(self, *_a: object, **_kw: object) -> _FakeCursor:
            return _FakeCursor()

        async def commit(self) -> None:
            return None

    def _fake_connect(*_a: object, **_kw: object) -> _FakeDB:
        return _FakeDB()

    store = SQLiteStore(tmp_path / "lastrow.sqlite")
    await store.init()
    monkeypatch.setattr(sqlite_module.aiosqlite, "connect", _fake_connect)

    with pytest.raises(McpEgrulError, match="Не удалось получить id строки import_log"):
        await store.start_import(
            started_at=_iso_now_utc(),
            source_dump_date="2026-04-24",
        )


# ---------------------------------------------------------------------------
# _normalize_bm25
# ---------------------------------------------------------------------------


def test_normalize_bm25_inf_maps_to_zero() -> None:
    assert _normalize_bm25(math.inf) == 0.0


def test_normalize_bm25_zero_or_negative_maps_to_one() -> None:
    assert _normalize_bm25(0.0) == 1.0
    assert _normalize_bm25(-1.5) == 1.0


def test_normalize_bm25_positive_maps_to_range() -> None:
    score = _normalize_bm25(3.0)
    assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# finish_import — инвариант status in {'success','failed'}.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_import_rejects_invalid_status(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "fi.sqlite")
    await store.init()

    import_id = await store.start_import(
        started_at=_iso_now_utc(),
        source_dump_date="2026-04-24",
    )

    with pytest.raises(McpEgrulError, match="Недопустимый финальный статус"):
        await store.finish_import(
            import_id,
            finished_at=_iso_now_utc(),
            records_imported=0,
            records_updated=0,
            errors_count=0,
            status="in-progress",  # не из разрешённого множества
        )


@pytest.mark.asyncio
async def test_store_ensure_auto_initialises_on_first_query(tmp_path: Path) -> None:
    """`_ensure` должен вызвать `init` на первом запросе без явного `init()`."""
    store = SQLiteStore(tmp_path / "autoinit.sqlite")
    # без await store.init() — сразу get_company_by_inn
    assert await store.get_company_by_inn("7707083893") is None
    # после этого БД инициализирована
    assert store._initialised is True
