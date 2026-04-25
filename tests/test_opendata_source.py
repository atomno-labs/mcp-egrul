"""Тесты `OpenDataSource.run_ingest`: сканирование каталогов, инкрементальная
логика, `import_log`."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from mcp_egrul.db import SQLiteStore
from mcp_egrul.errors import NothingToImportError, ValidationError
from mcp_egrul.sources.opendata import OpenDataSource

FIXTURES = Path(__file__).parent / "fixtures"


def _make_dumps_dir(root: Path, registry: str, iso_date: str) -> Path:
    date_dir = root / registry / iso_date
    date_dir.mkdir(parents=True, exist_ok=True)
    fixture_name = f"{registry}_sample.xml"
    shutil.copy(FIXTURES / fixture_name, date_dir / fixture_name)
    return date_dir


@pytest.fixture
def source() -> OpenDataSource:
    return OpenDataSource(user_agent="mcp-egrul-test", http_timeout_seconds=5.0)


async def test_full_ingest_egrul(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrul", "2026-04-01")

    report = await source.run_ingest(
        store, registry="egrul", dumps_dir=dumps_dir, full=True
    )
    assert report.registry == "egrul"
    assert report.source_dump_date == date(2026, 4, 1)
    assert report.records_imported == 2
    assert report.errors_count == 1

    counts = await store.count()
    assert counts["companies"] == 2
    assert counts["import_log"] == 1

    sber = await store.get_company_by_inn("7707083893")
    assert sber is not None
    assert sber["name_short"] == "ПАО СБЕРБАНК"
    assert sber["status"] == "active"


async def test_full_ingest_egrip(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrip", "2026-04-01")

    report = await source.run_ingest(
        store, registry="egrip", dumps_dir=dumps_dir, full=True
    )
    assert report.records_imported == 2
    assert report.errors_count == 0

    counts = await store.count()
    assert counts["individual_entrepreneurs"] == 2

    ivanov = await store.get_ie_by_ogrnip("304500116000061")
    assert ivanov is not None
    assert ivanov["fio"] == "Иванов Иван Иванович"

    petrova = await store.get_ie_by_ogrnip("320774000000048")
    assert petrova is not None
    assert petrova["status"] == "closed"


async def test_incremental_ingest_same_date_raises_nothing_to_import(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrul", "2026-04-01")

    await source.run_ingest(store, registry="egrul", dumps_dir=dumps_dir, full=True)

    with pytest.raises(NothingToImportError):
        await source.run_ingest(
            store, registry="egrul", dumps_dir=dumps_dir, full=False
        )


async def test_incremental_picks_newer_date(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrul", "2026-04-01")
    await source.run_ingest(store, registry="egrul", dumps_dir=dumps_dir, full=True)

    _make_dumps_dir(dumps_dir, "egrul", "2026-04-10")
    report = await source.run_ingest(
        store, registry="egrul", dumps_dir=dumps_dir, full=False
    )
    assert report.source_dump_date == date(2026, 4, 10)
    assert report.records_imported == 2


async def test_missing_registry_root_raises(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    dumps_dir.mkdir()
    with pytest.raises(ValidationError, match="Каталог дампов не найден"):
        await source.run_ingest(
            store, registry="egrul", dumps_dir=dumps_dir, full=True
        )


async def test_empty_date_dir_raises(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    date_dir = dumps_dir / "egrul" / "2026-04-01"
    date_dir.mkdir(parents=True)
    with pytest.raises(ValidationError, match="нет ни .xml, ни .zip"):
        await source.run_ingest(
            store, registry="egrul", dumps_dir=dumps_dir, full=True
        )


async def test_registry_root_without_date_dirs_raises(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    (dumps_dir / "egrul").mkdir(parents=True)
    (dumps_dir / "egrul" / "not-a-date").mkdir()
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        await source.run_ingest(
            store, registry="egrul", dumps_dir=dumps_dir, full=True
        )


async def test_unknown_registry_raises(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    dumps_dir = tmp_path / "dumps"
    dumps_dir.mkdir()
    with pytest.raises(ValidationError, match="Неподдерживаемый реестр"):
        await source.run_ingest(
            store, registry="bogus", dumps_dir=dumps_dir, full=True
        )


async def test_registry_root_ignores_non_dir_children(
    store: SQLiteStore, tmp_path: Path, source: OpenDataSource
) -> None:
    """Если в `dumps_dir/<registry>/` лежат файлы (а не папки-даты) — игнорируем.

    Закрывает ветку `if not child.is_dir(): continue` (строка ~215).
    """
    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrul", "2026-04-01")
    # Бросим мусорный файл прямо в `dumps/egrul/` — парсер должен его пропустить.
    (dumps_dir / "egrul" / "README.txt").write_text("ignore me", encoding="utf-8")

    report = await source.run_ingest(
        store, registry="egrul", dumps_dir=dumps_dir, full=True
    )
    assert report.source_dump_date == date(2026, 4, 1)
    assert report.records_imported == 2


async def test_run_ingest_marks_import_as_failed_on_unexpected_exception(
    store: SQLiteStore,
    tmp_path: Path,
    source: OpenDataSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Непредвиденная ошибка внутри `upsert_fn` — пишется `status=failed` в
    `import_log` и ошибка оборачивается в `McpEgrulError` (строки 154-166).
    """
    from mcp_egrul.errors import McpEgrulError

    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrul", "2026-04-01")

    async def _broken_upsert(_row: dict) -> None:
        raise RuntimeError("simulated disk full")

    monkeypatch.setattr(store, "upsert_company", _broken_upsert)

    with pytest.raises(McpEgrulError, match="Непредвиденная ошибка импорта"):
        await source.run_ingest(
            store, registry="egrul", dumps_dir=dumps_dir, full=True
        )

    import aiosqlite

    async with aiosqlite.connect(store._db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status FROM import_log ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["status"] == "failed"


async def test_run_ingest_logs_progress_every_batch_boundary(
    store: SQLiteStore,
    tmp_path: Path,
    source: OpenDataSource,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Понижаем `IMPORT_UPSERT_BATCH_LOG_SIZE` до 1 и убеждаемся,
    что лог-сообщение «imported so far: N» появляется минимум один раз.
    Закрывает ветку `if batch_for_log >= IMPORT_UPSERT_BATCH_LOG_SIZE:`
    (строки 115-121).
    """
    import logging

    import mcp_egrul.sources.opendata as opendata_module

    monkeypatch.setattr(opendata_module, "IMPORT_UPSERT_BATCH_LOG_SIZE", 1)

    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrul", "2026-04-01")

    with caplog.at_level(logging.INFO, logger="mcp_egrul.sources.opendata"):
        await source.run_ingest(
            store, registry="egrul", dumps_dir=dumps_dir, full=True
        )

    assert any("imported so far" in rec.message for rec in caplog.records)


async def test_run_ingest_marks_import_as_failed_on_mcp_egrul_error(
    store: SQLiteStore,
    tmp_path: Path,
    source: OpenDataSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`upsert` бросает прямой `McpEgrulError` (например, sqlite lock) —
    попадаем в ветку `except McpEgrulError` (строки 144-153): ставим
    `status=failed` в `import_log` и re-raise'им без дополнительной обёртки.
    """
    from mcp_egrul.errors import McpEgrulError

    dumps_dir = tmp_path / "dumps"
    _make_dumps_dir(dumps_dir, "egrul", "2026-04-01")

    async def _broken_upsert(_row: dict) -> None:
        raise McpEgrulError(
            "simulated sqlite lock",
            details={"kind": "OperationalError"},
        )

    monkeypatch.setattr(store, "upsert_company", _broken_upsert)

    with pytest.raises(McpEgrulError, match="simulated sqlite lock") as exc_info:
        await source.run_ingest(
            store, registry="egrul", dumps_dir=dumps_dir, full=True
        )
    # re-raise без обёртки — сообщение сохранилось как было
    assert "Непредвиденная" not in str(exc_info.value)

    import aiosqlite

    async with aiosqlite.connect(store._db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status FROM import_log ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["status"] == "failed"
