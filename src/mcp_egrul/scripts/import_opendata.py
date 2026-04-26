"""CLI `atomno-mcp-egrul-import`: импорт open-data дампов ФНС в локальный SQLite.

Поведение:
    * Читает `dumps_dir/<registry>/<YYYY-MM-DD>/*.(xml|zip)`, парсит
      поточно через lxml, заливает в SQLite через `SQLiteStore.upsert_*`.
    * По умолчанию — инкрементальный режим (`--incremental`): если самая
      свежая дата уже импортирована — CLI завершается с кодом 5 и явным
      сообщением «нечего импортировать».
    * Флаг `--full` включает принудительный переимпорт последней даты.

Коды возврата:
    0 — успех
    2 — ошибка валидации конфига или аргументов
    4 — ошибка ингеста (сеть / парсер / БД)
    5 — нечего импортировать (инкремент, самый свежий дамп уже в БД)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from ..config import Config
from ..context import ServiceContext
from ..errors import McpEgrulError, NothingToImportError, ValidationError
from ..sources import OpenDataSource

_EXIT_SUCCESS = 0
_EXIT_VALIDATION = 2
_EXIT_SOURCE_ERROR = 4
_EXIT_NOTHING_TO_DO = 5

logger = logging.getLogger("mcp_egrul.import")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atomno-mcp-egrul-import",
        description=(
            "Импорт ЕГРЮЛ/ЕГРИП open-data дампов ФНС в локальный SQLite. "
            "Положите скачанные .zip/.xml в "
            "dumps_dir/<registry>/<YYYY-MM-DD>/ и запустите команду."
        ),
    )
    parser.add_argument(
        "--registry",
        choices=["egrul", "egrip"],
        required=True,
        help="Какой реестр импортировать.",
    )
    mutex = parser.add_mutually_exclusive_group()
    mutex.add_argument(
        "--full",
        action="store_true",
        help="Принудительный переимпорт самой свежей выгрузки (first-run или fix).",
    )
    mutex.add_argument(
        "--incremental",
        action="store_true",
        help="Инкрементальный импорт (по умолчанию): только если есть более свежая "
        "дата, чем предыдущий успешный импорт.",
    )
    return parser


async def _run(registry: str, *, full: bool) -> int:
    try:
        config = Config.from_env()
    except ValidationError as exc:
        logger.error("Конфигурация невалидна: %s", exc.message_ru)
        return _EXIT_VALIDATION

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
                full=full,
            )
        except NothingToImportError as exc:
            logger.info("nothing_to_import: %s", exc.message_ru)
            if exc.hint:
                logger.info("hint: %s", exc.hint)
            return _EXIT_NOTHING_TO_DO
        except McpEgrulError as exc:
            logger.error("Ошибка ингеста: %s", exc.message_ru)
            if exc.hint:
                logger.info("hint: %s", exc.hint)
            return _EXIT_SOURCE_ERROR

    logger.info(
        "Ингест завершён: registry=%s imported=%d updated=%d errors=%d "
        "(source_date=%s)",
        report.registry,
        report.records_imported,
        report.records_updated,
        report.errors_count,
        report.source_dump_date.isoformat(),
    )
    return _EXIT_SUCCESS


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    full = bool(args.full)
    code = asyncio.run(_run(args.registry, full=full))
    return code


if __name__ == "__main__":
    sys.exit(main())
