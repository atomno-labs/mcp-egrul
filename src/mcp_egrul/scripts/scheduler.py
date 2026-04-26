"""CLI `atomno-mcp-egrul-scheduler`: cron-демон для ежедневного импорта ЕГРЮЛ/ЕГРИП.

Запускается как отдельный процесс (в docker-compose — отдельный сервис).
Крутит внутри себя `AsyncIOScheduler` из `apscheduler` с двумя cron-job'ами:

    * `mcp-egrul-daily-egrul` — каждый день в 03:00 Europe/Moscow — инкрементально.
    * `mcp-egrul-daily-egrip` — каждый день в 03:00 Europe/Moscow — инкрементально.

Поведение при старте:
    * Логгер настраивается на уровень из `MCP_EGRUL_LOG_LEVEL`.
    * Если `MCP_EGRUL_DUMPS_DIR` ещё пуст — лог warning, но процесс
      не падает (ждём, пока оператор положит дампы).
    * `NothingToImportError` — ожидаемое поведение, логируется на INFO,
      не считается падением job'а.
    * `McpEgrulError` на каком-то job'е — log error, job выпадает, но
      scheduler продолжает крутиться (другие job'ы живы).

Остановка:
    Ctrl+C (SIGINT) — scheduler корректно останавливается, текущий
    запущенный job дорабатывает до конца.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Config
from ..constants import (
    IMPORT_SUPPORTED_REGISTRIES,
    SCHEDULER_CRON_HOUR,
    SCHEDULER_CRON_MINUTE,
    SCHEDULER_JOB_ID_EGRIP,
    SCHEDULER_JOB_ID_EGRUL,
    SCHEDULER_MISFIRE_GRACE_SECONDS,
    SCHEDULER_TIMEZONE,
)
from ..context import ServiceContext
from ..errors import McpEgrulError, NothingToImportError, ValidationError
from ..sources import OpenDataSource

logger = logging.getLogger("mcp_egrul.scheduler")


async def _run_daily_ingest(registry: str) -> None:
    """Один цикл ежедневного импорта одного реестра (инкрементально)."""
    config = Config.from_env()
    ctx = ServiceContext.from_config(config)
    async with ctx:
        source = OpenDataSource(
            user_agent=config.user_agent,
            http_timeout_seconds=config.http_timeout_seconds,
        )
        try:
            report = await source.run_ingest(
                ctx.store,
                registry=registry,
                dumps_dir=config.dumps_dir,
                full=False,
            )
        except NothingToImportError as exc:
            logger.info(
                "[%s] nothing_to_import: %s", registry, exc.message_ru
            )
            return
        except McpEgrulError as exc:
            logger.error(
                "[%s] ingest failed: %s (details=%s)",
                registry,
                exc.message_ru,
                exc.details,
            )
            return
    logger.info(
        "[%s] ingest ok: imported=%d updated=%d errors=%d source_date=%s",
        registry,
        report.records_imported,
        report.records_updated,
        report.errors_count,
        report.source_dump_date.isoformat(),
    )


def _register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Зарегистрировать cron-job'ы для каждого реестра."""
    job_ids = {
        "egrul": SCHEDULER_JOB_ID_EGRUL,
        "egrip": SCHEDULER_JOB_ID_EGRIP,
    }
    missing = set(IMPORT_SUPPORTED_REGISTRIES) - set(job_ids)
    if missing:
        raise ValidationError(
            (
                "IMPORT_SUPPORTED_REGISTRIES содержит реестры без "
                f"scheduler-job-id: {sorted(missing)}."
            ),
            hint="Добавьте SCHEDULER_JOB_ID_* в constants.py.",
            details={"missing": sorted(missing)},
        )

    trigger = CronTrigger(
        hour=SCHEDULER_CRON_HOUR,
        minute=SCHEDULER_CRON_MINUTE,
        timezone=SCHEDULER_TIMEZONE,
    )
    for registry in IMPORT_SUPPORTED_REGISTRIES:
        scheduler.add_job(
            _run_daily_ingest,
            trigger=trigger,
            kwargs={"registry": registry},
            id=job_ids[registry],
            misfire_grace_time=SCHEDULER_MISFIRE_GRACE_SECONDS,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "registered job %s: cron %02d:%02d %s",
            job_ids[registry],
            SCHEDULER_CRON_HOUR,
            SCHEDULER_CRON_MINUTE,
            SCHEDULER_TIMEZONE,
        )


async def _run_scheduler(*, run_now: bool) -> int:
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
    _register_jobs(scheduler)
    scheduler.start()

    if run_now:
        logger.info("run_now=True → запускаю ingest всех реестров немедленно")
        for registry in IMPORT_SUPPORTED_REGISTRIES:
            await _run_daily_ingest(registry)

    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("получен сигнал остановки, scheduler shutdown...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows: signal handler через loop недоступен — ловим Ctrl+C
            # штатно через KeyboardInterrupt в await stop_event.wait().
            pass

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        scheduler.shutdown(wait=True)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atomno-mcp-egrul-scheduler",
        description=(
            "Cron-демон для ежедневного импорта ЕГРЮЛ/ЕГРИП (03:00 "
            "Europe/Moscow, инкрементально)."
        ),
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help=(
            "Запустить ingest всех реестров немедленно (до первого cron-тика). "
            "Полезно при старте контейнера, когда cron сработает только ночью."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_run_scheduler(run_now=bool(args.run_now)))


if __name__ == "__main__":
    sys.exit(main())
