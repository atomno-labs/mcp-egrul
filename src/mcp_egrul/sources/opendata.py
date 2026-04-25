"""Адаптер официальных open-data дампов ФНС для ЕГРЮЛ/ЕГРИП.

Источники (SPEC §5.3):
    * ЕГРЮЛ: https://www.nalog.gov.ru/opendata/7707329152-egrul/
    * ЕГРИП: https://www.nalog.gov.ru/opendata/7707329152-egrip/

Формат — суточные архивы XML в ZIP, объём ~15 ГБ на полный слепок.
Мы **не** качаем архивы сами — это право/обязанность оператора self-host
(лицензионное соглашение ФНС требует acceptance через их сайт). Мы
читаем `.xml`/`.zip` файлы, которые пользователь сам положил в
`dumps_dir/<registry>/<date>/`.

Структура ожидаемого каталога::

    dumps/
        egrul/
            2026-04-24/             # ISO-дата выгрузки ФНС (source_date)
                EGRUL_01.xml.zip
                EGRUL_02.xml.zip
                ...
        egrip/
            2026-04-24/
                EGRIP_01.xml.zip

Поведение:
    * `full=True`  — пройти по всем файлам последнего датированного
                     каталога и upsert-нуть весь слепок.
    * `full=False` — инкрементальный режим: взять только самую свежую
                     дату-папку после последнего успешного импорта (по
                     таблице `import_log`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from datetime import date as DateT
from pathlib import Path

from ..constants import (
    IMPORT_DATE_DIR_FORMAT,
    IMPORT_SUPPORTED_REGISTRIES,
    IMPORT_UPSERT_BATCH_LOG_SIZE,
)
from ..db import SQLiteStore
from ..errors import McpEgrulError, NothingToImportError, ValidationError
from .base import IngestReport, Source
from .opendata_parser import ParseStats, iter_dump_records

logger = logging.getLogger(__name__)


class OpenDataSource(Source):
    """Адаптер ФНС open-data дампов (чтение из локального каталога)."""

    name: str = "opendata"

    def __init__(self, user_agent: str, http_timeout_seconds: float) -> None:
        self._user_agent = user_agent
        self._http_timeout = http_timeout_seconds

    async def run_ingest(
        self,
        store: SQLiteStore,
        *,
        registry: str,
        dumps_dir: Path,
        full: bool,
    ) -> IngestReport:
        if registry not in IMPORT_SUPPORTED_REGISTRIES:
            raise ValidationError(
                (
                    f"Неподдерживаемый реестр '{registry}'. "
                    f"Допустимо: {sorted(IMPORT_SUPPORTED_REGISTRIES)}."
                ),
                details={"registry": registry},
            )

        started_at = _now_iso_utc()
        date_dir, source_date = await self._resolve_date_dir_async(
            dumps_dir=dumps_dir, registry=registry, full=full, store=store
        )
        dump_files = self._list_dump_files(date_dir)

        import_log_id = await store.start_import(
            source_dump_date=source_date.isoformat(),
            started_at=started_at,
        )

        imported = 0
        updated = 0
        errors_count = 0

        try:
            upsert_fn = (
                store.upsert_company if registry == "egrul" else store.upsert_ie
            )
            existing_before = await _count_existing(store, registry=registry)

            for dump_path in dump_files:
                logger.info(
                    "parsing dump %s (registry=%s)", dump_path.name, registry
                )
                stats = ParseStats()
                batch_for_log = 0
                for record in iter_dump_records(
                    dump_path,
                    registry=registry,
                    source_date=source_date,
                    stats=stats,
                ):
                    await upsert_fn(record)
                    imported += 1
                    batch_for_log += 1
                    if batch_for_log >= IMPORT_UPSERT_BATCH_LOG_SIZE:
                        logger.info(
                            "imported so far: %d records (%s)",
                            imported,
                            dump_path.name,
                        )
                        batch_for_log = 0
                errors_count += len(stats.errors)
                for err in stats.errors:
                    logger.warning(
                        "skipped %s record %s: %s",
                        err.registry,
                        err.record_id,
                        err.message,
                    )

            existing_after = await _count_existing(store, registry=registry)
            updated = max(0, existing_after - existing_before)
            updated = min(updated, imported)

            finished_at = _now_iso_utc()
            await store.finish_import(
                import_log_id,
                finished_at=finished_at,
                records_imported=imported,
                records_updated=updated,
                errors_count=errors_count,
                status="success",
            )
        except McpEgrulError:
            await store.finish_import(
                import_log_id,
                finished_at=_now_iso_utc(),
                records_imported=imported,
                records_updated=updated,
                errors_count=errors_count,
                status="failed",
            )
            raise
        except Exception as exc:
            await store.finish_import(
                import_log_id,
                finished_at=_now_iso_utc(),
                records_imported=imported,
                records_updated=updated,
                errors_count=errors_count,
                status="failed",
            )
            raise McpEgrulError(
                f"Непредвиденная ошибка импорта: {exc}",
                details={"registry": registry, "date_dir": str(date_dir)},
            ) from exc

        return IngestReport(
            registry=registry,
            source_dump_date=source_date,
            records_imported=imported,
            records_updated=updated,
            errors_count=errors_count,
            started_at=started_at,
            finished_at=finished_at,
        )

    # ---- helpers ------------------------------------------------------

    async def _resolve_date_dir_async(
        self,
        *,
        dumps_dir: Path,
        registry: str,
        full: bool,
        store: SQLiteStore,
    ) -> tuple[Path, DateT]:
        """Определить каталог дампа по дате и вернуть (path, date).

        * `full=True`  — самая свежая `YYYY-MM-DD` папка (принудительно).
        * `full=False` — самая свежая папка, но только если её дата > даты
                         последнего успешного импорта. Иначе — явная
                         `McpEgrulError` «нечего импортировать», чтобы cron
                         не делал лишнюю работу молча.
        """
        registry_root = dumps_dir / registry
        if not registry_root.exists() or not registry_root.is_dir():
            raise ValidationError(
                (
                    f"Каталог дампов не найден: {registry_root}. "
                    f"Создайте его и положите туда подкаталог 'YYYY-MM-DD' "
                    f"с архивами ФНС."
                ),
                hint=(
                    "Архивы ЕГРЮЛ/ЕГРИП скачиваются вручную на "
                    "https://www.nalog.gov.ru/opendata/ после принятия "
                    "лицензионного соглашения."
                ),
                details={"dumps_dir": str(dumps_dir), "registry": registry},
            )

        date_dirs: list[tuple[DateT, Path]] = []
        for child in registry_root.iterdir():
            if not child.is_dir():
                continue
            try:
                parsed = datetime.strptime(child.name, IMPORT_DATE_DIR_FORMAT).date()
            except ValueError:
                continue
            date_dirs.append((parsed, child))

        if not date_dirs:
            raise ValidationError(
                (
                    f"В {registry_root} нет ни одного подкаталога вида "
                    f"YYYY-MM-DD с архивами."
                ),
                hint="Создайте подпапку с датой выгрузки ФНС.",
                details={"registry_root": str(registry_root)},
            )

        date_dirs.sort(key=lambda p: p[0], reverse=True)
        chosen_date, chosen_path = date_dirs[0]

        if not full:
            last = await store.last_successful_import_date()
            if last is not None and chosen_date.isoformat() <= last:
                raise NothingToImportError(
                    (
                        f"Нечего импортировать: в {registry_root} самая свежая "
                        f"выгрузка {chosen_date.isoformat()} уже импортирована "
                        f"(last_success={last})."
                    ),
                    hint=(
                        "Скачайте свежий дамп ФНС и положите его в новую "
                        "подпапку-YYYY-MM-DD или запустите с --full для "
                        "принудительного переимпорта."
                    ),
                    details={
                        "registry": registry,
                        "chosen_date": chosen_date.isoformat(),
                        "last_success": last,
                    },
                )

        return chosen_path, chosen_date

    def _list_dump_files(self, date_dir: Path) -> list[Path]:
        """Вернуть список `.xml` и `.zip` в директории даты, отсортированный."""
        files = sorted(
            p
            for p in date_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (".xml", ".zip")
        )
        if not files:
            raise ValidationError(
                (
                    f"В каталоге {date_dir} нет ни .xml, ни .zip файлов "
                    f"для импорта."
                ),
                details={"date_dir": str(date_dir)},
            )
        return files


def _now_iso_utc() -> str:
    return datetime.now(tz=UTC).isoformat()


async def _count_existing(store: SQLiteStore, *, registry: str) -> int:
    counts = await store.count()
    table = "companies" if registry == "egrul" else "individual_entrepreneurs"
    return counts.get(table, 0)
