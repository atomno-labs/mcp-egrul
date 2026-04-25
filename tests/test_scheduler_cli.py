"""Тесты CLI `mcp-egrul-scheduler`: регистрация cron-job'ов, парсинг аргументов,
поведение `_run_daily_ingest` при разных исходах ingest'а.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from mcp_egrul.constants import (
    IMPORT_SUPPORTED_REGISTRIES,
    SCHEDULER_CRON_HOUR,
    SCHEDULER_CRON_MINUTE,
    SCHEDULER_JOB_ID_EGRIP,
    SCHEDULER_JOB_ID_EGRUL,
    SCHEDULER_TIMEZONE,
)
from mcp_egrul.errors import McpEgrulError, NothingToImportError, ValidationError
from mcp_egrul.scripts import scheduler as scheduler_module
from mcp_egrul.scripts.scheduler import (
    _build_parser,
    _register_jobs,
    _run_daily_ingest,
    main,
)
from mcp_egrul.sources.base import IngestReport

FIXTURES = Path(__file__).parent / "fixtures"


def test_register_jobs_creates_one_per_registry() -> None:
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
    _register_jobs(scheduler)
    jobs = scheduler.get_jobs()
    job_ids = {job.id for job in jobs}
    assert len(jobs) == len(IMPORT_SUPPORTED_REGISTRIES)
    assert SCHEDULER_JOB_ID_EGRUL in job_ids
    assert SCHEDULER_JOB_ID_EGRIP in job_ids


def test_register_jobs_cron_trigger_fields() -> None:
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
    _register_jobs(scheduler)
    job = scheduler.get_job(SCHEDULER_JOB_ID_EGRUL)
    trigger = job.trigger
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == str(SCHEDULER_CRON_HOUR)
    assert fields["minute"] == str(SCHEDULER_CRON_MINUTE)
    assert str(trigger.timezone) == SCHEDULER_TIMEZONE


def test_register_jobs_rejects_registry_without_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если кто-то добавил реестр в IMPORT_SUPPORTED_REGISTRIES, но забыл
    SCHEDULER_JOB_ID_* — это должно быть поймано как ValidationError, а не
    silent skip.
    """
    monkeypatch.setattr(
        scheduler_module,
        "IMPORT_SUPPORTED_REGISTRIES",
        ("egrul", "egrip", "foo-bar-unknown-registry"),
    )
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
    with pytest.raises(ValidationError) as exc_info:
        _register_jobs(scheduler)
    assert "foo-bar-unknown-registry" in exc_info.value.details["missing"]


# ---------------------------------------------------------------------------
# _build_parser
# ---------------------------------------------------------------------------


def test_build_parser_run_now_flag() -> None:
    parser = _build_parser()
    ns = parser.parse_args(["--run-now"])
    assert ns.run_now is True
    ns_default = parser.parse_args([])
    assert ns_default.run_now is False


# ---------------------------------------------------------------------------
# _run_daily_ingest
# ---------------------------------------------------------------------------


def _prepare_dumps_dir(root: Path, registry: str, iso_date: str) -> None:
    target = root / registry / iso_date
    target.mkdir(parents=True, exist_ok=True)
    fixture = FIXTURES / f"{registry}_sample.xml"
    shutil.copy(fixture, target / fixture.name)


@pytest.mark.asyncio
async def test_run_daily_ingest_happy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "sched.sqlite"
    dumps_dir = tmp_path / "dumps"
    _prepare_dumps_dir(dumps_dir, "egrul", "2026-04-01")
    monkeypatch.setenv("MCP_EGRUL_DB", str(db_path))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(dumps_dir))

    # Первый прогон — full (чтобы отметка в import_log появилась).
    # Для этого подкручиваем OpenDataSource.run_ingest: прогоняем реальный
    # full, потом в следующем тесте снова «инкремент».
    # Здесь же тестируем только что метод вызывается и не падает на
    # первом incremental-прогоне (когда last_success пуст → считает full).
    await _run_daily_ingest("egrul")


@pytest.mark.asyncio
async def test_run_daily_ingest_logs_nothing_to_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging as _logging

    class _FakeSource:
        def __init__(self, **_: object) -> None:
            pass

        async def run_ingest(self, *_args: object, **_kwargs: object) -> None:
            raise NothingToImportError(
                "нечего импортировать",
                details={"registry": "egrul"},
            )

    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "n2i.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))
    monkeypatch.setattr(scheduler_module, "OpenDataSource", _FakeSource)

    caplog.set_level(_logging.INFO, logger="mcp_egrul.scheduler")
    await _run_daily_ingest("egrul")

    assert any("nothing_to_import" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_run_daily_ingest_logs_mcp_error_but_doesnt_raise(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging as _logging

    class _BrokenSource:
        def __init__(self, **_: object) -> None:
            pass

        async def run_ingest(self, *_args: object, **_kwargs: object) -> None:
            raise McpEgrulError(
                "DB недоступна",
                details={"registry": "egrul"},
            )

    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "err.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))
    monkeypatch.setattr(scheduler_module, "OpenDataSource", _BrokenSource)

    caplog.set_level(_logging.ERROR, logger="mcp_egrul.scheduler")
    await _run_daily_ingest("egrul")

    assert any(
        "ingest failed" in rec.message and rec.levelno == _logging.ERROR
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_run_daily_ingest_logs_success_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging as _logging

    class _OkSource:
        def __init__(self, **_: object) -> None:
            pass

        async def run_ingest(
            self, *_args: object, **_kwargs: object
        ) -> IngestReport:
            return IngestReport(
                registry="egrul",
                source_dump_date=date(2026, 4, 24),
                records_imported=2,
                records_updated=1,
                errors_count=0,
                started_at="2026-04-24T03:00:00+00:00",
                finished_at="2026-04-24T03:01:00+00:00",
            )

    monkeypatch.setenv("MCP_EGRUL_DB", str(tmp_path / "ok.sqlite"))
    monkeypatch.setenv("MCP_EGRUL_DUMPS_DIR", str(tmp_path / "dumps"))
    monkeypatch.setattr(scheduler_module, "OpenDataSource", _OkSource)

    caplog.set_level(_logging.INFO, logger="mcp_egrul.scheduler")
    await _run_daily_ingest("egrul")

    assert any(
        "ingest ok" in rec.message and "imported=2" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# _run_scheduler (полный цикл старта/остановки без бесконечного await).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scheduler_full_cycle_with_run_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Полный цикл `_run_scheduler(run_now=True)`:
    регистрирует job'ы, запускает scheduler, прогоняет ingest по всем реестрам,
    затем останавливается по pre-set `stop_event`.

    Тест подменяет `asyncio.Event` так, чтобы `stop_event.wait()` возвращался
    сразу, и мокает `_run_daily_ingest`, чтобы не трогать ни БД, ни файловую
    систему.
    """
    import asyncio as _asyncio

    registries_called: list[str] = []

    async def _fake_ingest(registry: str) -> None:
        registries_called.append(registry)

    class _InstantEvent(_asyncio.Event):
        def __init__(self) -> None:
            super().__init__()
            self.set()

    monkeypatch.setattr(scheduler_module, "_run_daily_ingest", _fake_ingest)
    monkeypatch.setattr(scheduler_module.asyncio, "Event", _InstantEvent)

    code = await scheduler_module._run_scheduler(run_now=True)
    assert code == 0
    assert registries_called == list(IMPORT_SUPPORTED_REGISTRIES)


@pytest.mark.asyncio
async def test_run_scheduler_without_run_now_does_not_trigger_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio as _asyncio

    registries_called: list[str] = []

    async def _fake_ingest(registry: str) -> None:  # pragma: no cover - guarded
        registries_called.append(registry)

    class _InstantEvent(_asyncio.Event):
        def __init__(self) -> None:
            super().__init__()
            self.set()

    monkeypatch.setattr(scheduler_module, "_run_daily_ingest", _fake_ingest)
    monkeypatch.setattr(scheduler_module.asyncio, "Event", _InstantEvent)

    code = await scheduler_module._run_scheduler(run_now=False)
    assert code == 0
    assert registries_called == []


@pytest.mark.asyncio
async def test_run_scheduler_signal_handler_sets_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Покрываем `_handle_signal` (строки 144-145):
    подменяем `loop.add_signal_handler` так, чтобы он сразу вызвал переданный
    callback (как будто сигнал уже пришёл в момент регистрации). Callback
    должен сделать `stop_event.set()` и scheduler должен корректно выйти.
    """
    import asyncio as _asyncio

    async def _fake_ingest(registry: str) -> None:
        return None

    monkeypatch.setattr(scheduler_module, "_run_daily_ingest", _fake_ingest)

    original_get_loop = scheduler_module.asyncio.get_running_loop

    class _LoopProxy:
        def __init__(self, real: object) -> None:
            self._real = real

        def add_signal_handler(self, _sig: int, callback: object) -> None:
            # Симулируем «сигнал пришёл прямо сейчас»: вызываем callback,
            # чтобы он set'нул stop_event в теле _run_scheduler.
            callback()  # type: ignore[misc]

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    def _fake_get_running_loop() -> _LoopProxy:
        return _LoopProxy(original_get_loop())

    monkeypatch.setattr(
        scheduler_module.asyncio, "get_running_loop", _fake_get_running_loop
    )

    code = await _asyncio.wait_for(
        scheduler_module._run_scheduler(run_now=False), timeout=2.0
    )
    assert code == 0


@pytest.mark.asyncio
async def test_run_scheduler_skips_missing_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Покрываем строку 151 (`if sig is None: continue`):
    убираем `SIGTERM` из `signal`-модуля (как на некоторых Windows-сборках),
    scheduler должен пропустить его и не упасть.
    """
    import asyncio as _asyncio

    class _InstantEvent(_asyncio.Event):
        def __init__(self) -> None:
            super().__init__()
            self.set()

    monkeypatch.setattr(scheduler_module.asyncio, "Event", _InstantEvent)
    monkeypatch.delattr(scheduler_module.signal, "SIGTERM", raising=False)

    code = await scheduler_module._run_scheduler(run_now=False)
    assert code == 0


@pytest.mark.asyncio
async def test_run_scheduler_handles_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Покрываем строки 161-162 (`except (CancelledError, KeyboardInterrupt)`):
    Event.wait() бросает CancelledError — scheduler корректно делает shutdown.
    """
    import asyncio as _asyncio

    class _RaisingEvent:
        def set(self) -> None:  # вызывается сигнал-хендлером
            pass

        def is_set(self) -> bool:
            return False

        async def wait(self) -> None:
            raise _asyncio.CancelledError()

    monkeypatch.setattr(scheduler_module.asyncio, "Event", _RaisingEvent)

    code = await scheduler_module._run_scheduler(run_now=False)
    assert code == 0


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_invokes_run_scheduler_via_asyncio_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main()` должен прокинуть `--run-now` в `_run_scheduler` и вернуть код."""
    called_with: dict[str, bool] = {}

    async def _fake_run_scheduler(*, run_now: bool) -> int:
        called_with["run_now"] = run_now
        return 0

    monkeypatch.setattr(scheduler_module, "_run_scheduler", _fake_run_scheduler)
    code = main(["--run-now"])
    assert code == 0
    assert called_with == {"run_now": True}

    called_with.clear()
    code = main([])
    assert code == 0
    assert called_with == {"run_now": False}
