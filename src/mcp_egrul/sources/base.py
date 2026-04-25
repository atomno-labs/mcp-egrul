"""Контракт адаптера источника данных для mcp-egrul.

Каждый `Source` умеет запустить полный или инкрементальный ингест в
`SQLiteStore`. Тулзы НЕ обращаются к `Source` напрямую — только CLI
`mcp-egrul-import` и (позже) cron-скрипт дневного обновления.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date as DateT
from pathlib import Path

from ..db import SQLiteStore


@dataclass(frozen=True)
class IngestReport:
    """Отчёт об одном прогоне ингеста."""

    registry: str                    # 'egrul' | 'egrip'
    source_dump_date: DateT
    records_imported: int
    records_updated: int
    errors_count: int
    started_at: str                  # ISO UTC
    finished_at: str                 # ISO UTC


class Source(ABC):
    """Базовый интерфейс источника.

    Реализация отвечает за:
        1. Скачивание / чтение дампа из локального каталога.
        2. Парсинг во внутренние dict-строки, совместимые со схемой
           `companies` / `individual_entrepreneurs`.
        3. Запись в `SQLiteStore` через `upsert_company` / `upsert_ie`.
        4. Добавление записи в `import_log`.
    """

    name: str = "abstract-source"

    @abstractmethod
    async def run_ingest(
        self,
        store: SQLiteStore,
        *,
        registry: str,
        dumps_dir: Path,
        full: bool,
    ) -> IngestReport:
        """Выполнить ингест.

        Args:
            store: целевой SQLite-store, уже инициализированный.
            registry: 'egrul' или 'egrip' — какой реестр импортировать.
            dumps_dir: каталог, в который скачиваются архивы.
            full: True — полный ингест (пересобрать всё), False —
                только инкремент (дельта за день).

        Returns:
            IngestReport со статистикой.

        Raises:
            NothingToImportError: инкремент, но новых дампов нет
                (не падение, а корректный «нечего делать»-сигнал).
            SourceUnavailableError / McpEgrulError: при ошибках сети/парсинга/БД.
        """
