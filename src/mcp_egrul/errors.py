"""Типизированные исключения пакета mcp-egrul.

Иерархия (см. SPEC §4.3 и §5.4.1):

    McpEgrulError               — корень
        ValidationError         — невалидный вход (ИНН/ОГРН не проходит контроль)
        NotFoundError           — запись не найдена в локальном слепке/источнике
        SourceUnavailableError  — внешний источник недоступен (5xx/таймаут/DNS)
        RateLimitedError        — превышен rate-limit провайдера
        BulkTooLargeError       — запрошено больше `BULK_CARDS_MAX_INNS` ИНН
        HostedAuthError         — hosted API: ключ отсутствует/невалидный (401)
        ProRequiredError        — hosted API: Pro-фича на Free-ключе (403)
        NotImplementedInPhase   — функциональность намеренно недоступна в
                                  текущей фазе; используется вместо
                                  `NotImplementedError` чтобы не маскировать
                                  настоящие дыры и сохранять MCP-контракт.
        NothingToImportError    — инкремент-импорт нашёл 0 новых дампов

Каждое исключение несёт:
    * `message_ru` — человекочитаемое сообщение для AI-агента;
    * `hint`      — подсказка, что пользователю сделать дальше (необязательно);
    * `details`   — произвольная структурированная диагностика.
"""

from __future__ import annotations

from typing import Any

from .constants import (
    ERROR_CODE_AUTH_REQUIRED,
    ERROR_CODE_BULK_TOO_LARGE,
    ERROR_CODE_INTERNAL,
    ERROR_CODE_NOT_FOUND,
    ERROR_CODE_NOT_IMPLEMENTED,
    ERROR_CODE_PRO_REQUIRED,
    ERROR_CODE_RATE_LIMIT,
    ERROR_CODE_SOURCE_UNAVAILABLE,
    ERROR_CODE_VALIDATION,
)


class McpEgrulError(Exception):
    """Базовое исключение пакета.

    Имеет стабильный `code` (см. `constants.ERROR_CODE_*`) — клиенты MCP
    разбирают ответы по нему, а не по тексту сообщения.
    """

    code: str = ERROR_CODE_INTERNAL

    def __init__(
        self,
        message_ru: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message_ru = message_ru
        self.hint = hint
        self.details = details or {}
        super().__init__(message_ru)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": True,
            "code": self.code,
            "message": self.message_ru,
        }
        if self.hint is not None:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(McpEgrulError):
    code = ERROR_CODE_VALIDATION


class NotFoundError(McpEgrulError):
    code = ERROR_CODE_NOT_FOUND


class SourceUnavailableError(McpEgrulError):
    code = ERROR_CODE_SOURCE_UNAVAILABLE


class RateLimitedError(McpEgrulError):
    code = ERROR_CODE_RATE_LIMIT


class BulkTooLargeError(McpEgrulError):
    code = ERROR_CODE_BULK_TOO_LARGE


class HostedAuthError(McpEgrulError):
    """Hosted API отверг запрос из-за аутентификации (HTTP 401).

    Типовые причины:
      * `ATOMNO_API_KEY` не задан, но hosted_mode_enabled выставлен кем-то
        извне (руками редактировали конфиг) — в `Config.from_env` такого
        не бывает, только при ручном конструировании `HostedClient`.
      * Ключ отозван / истёк / опечатка.

    Не должно автоматически фолбэчить на локальный SQLite — это молчаливое
    деградирование качества данных (SPEC §5.4.1, блок про timeouts).
    """

    code = ERROR_CODE_AUTH_REQUIRED


class ProRequiredError(McpEgrulError):
    """Hosted API: ключ валидный, но tier=free (HTTP 403).

    Возникает при попытке вызвать Pro-only тул с Free-ключом. Не должно
    заменяться на локальный ответ — фича намеренно закрыта биллингом.
    """

    code = ERROR_CODE_PRO_REQUIRED


class NotImplementedInPhase(McpEgrulError):
    """Функциональность намеренно не реализована в текущей фазе.

    Используется вместо голого `NotImplementedError`, чтобы:
      1. Ответ MCP-тулза был валидным JSON-объектом с понятным `code` и
         `message` — а не внутренней traceback-ошибкой.
      2. В логах однозначно читалось «это не баг, это Phase 0 граница» —
         без silent fallback на мусор.
    """

    code = ERROR_CODE_NOT_IMPLEMENTED


class NothingToImportError(McpEgrulError):
    """Инкрементальный импорт не нашёл более свежего дампа, чем уже в БД.

    Отдельный подкласс, чтобы CLI `mcp-egrul-import` мог различать «нечего
    делать» (код выхода 5, cron-friendly) и «реально сломалось» — без
    парсинга текста сообщения.
    """

    code = "nothing_to_import"
