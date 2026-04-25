"""HostedClient — прокси в hosted Pro API (SPEC §5.4, §5.4.1).

Работает только когда пользователь задал `ATOMNO_API_KEY`. Контракт
полностью описан в SPEC §5.4.1 — здесь мы его РЕАЛИЗУЕМ со стороны
клиента. Будущий `apps/mcp-egrul-server/` (Phase 2) обязан отвечать
на те же пути и возвращать те же payload'ы.

Принципы:
    * **Без silent fallback на локальный SQLite.** Если hosted-режим
      включён, а сеть/авторизация/rate-limit сорвались — клиент
      поднимает типизированное `McpEgrulError`. Иначе AI-агент будет
      думать, что ему дали свежие данные, а на самом деле они из
      устаревшего дампа.
    * **Нулевой retry в клиенте.** Retry делает FastMCP/агент на своём
      уровне, а мы не маскируем 5xx повторами — `SourceUnavailableError`
      с `details.cause` передаёт реальную картину.
    * **Валидация ответов — строгая.** Pydantic-модели из `schemas.py`,
      неизвестные поля отбрасываются (`extra="ignore"` в `StrictModel`).
      Если сервер прислал мусор — `SourceUnavailableError`, не молча
      подсовываем пользователю «полупустую» карточку.

Открытие/закрытие:
    Клиент — `AsyncContextManager`; в `ServiceContext` он живёт рядом
    со `SQLiteStore` и закрывается в `__aexit__`. Внутри `httpx.AsyncClient`
    переиспользует один keep-alive пул соединений.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..constants import BULK_CARDS_MAX_INNS
from ..errors import (
    BulkTooLargeError,
    HostedAuthError,
    NotFoundError,
    ProRequiredError,
    RateLimitedError,
    SourceUnavailableError,
    ValidationError,
)
from ..schemas import (
    BulkResult,
    CompanyCard,
    Director,
    Founder,
    IECard,
    SearchHit,
)

_HTTP_AUTH = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_BULK_TOO_LARGE = 413
_HTTP_RATE_LIMIT = 429
_HTTP_4XX_LOW = 400
_HTTP_5XX_LOW = 500


class HostedClient:
    """Тонкий HTTP-клиент к hosted Pro API.

    Создаётся один раз на процесс; методы — строго `async`, каждый
    соответствует одному MCP-тулзу из SPEC §4.1 (маппинг на эндпойнты —
    SPEC §5.4.1). Подписи методов совпадают с локальными реализациями
    в `tools/*.py`, чтобы маршрутизация в `ServiceContext`-зависимых
    тулзах была просто `if ctx.hosted_client: return await ctx.hosted_client.<method>(...)`.
    """

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        http_timeout_seconds: float,
        user_agent: str,
    ) -> None:
        if not api_key:
            raise ValidationError(
                "HostedClient: ATOMNO_API_KEY пуст.",
                hint="Передайте валидный Pro-ключ или отключите hosted-режим.",
                details={"reason": "empty_api_key"},
            )
        if not api_base:
            raise ValidationError(
                "HostedClient: ATOMNO_API_BASE пуст.",
                details={"reason": "empty_api_base"},
            )
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self._api_base,
            timeout=http_timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
        )
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        await self._client.aclose()
        self._closed = True

    async def __aenter__(self) -> HostedClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -----------------------------------------------------------------
    # Public methods — один метод = один MCP-тулз (SPEC §4.1, §5.4.1).
    # -----------------------------------------------------------------

    async def search_by_inn(self, inn: str) -> CompanyCard | IECard:
        """`GET /companies/inn/{inn}` → `CompanyCard | IECard`."""
        payload = await self._get_json(f"/companies/inn/{inn}")
        return self._parse_card(payload)

    async def search_by_ogrn(self, ogrn: str) -> CompanyCard | IECard:
        """`GET /companies/ogrn/{ogrn}` → `CompanyCard | IECard`."""
        payload = await self._get_json(f"/companies/ogrn/{ogrn}")
        return self._parse_card(payload)

    async def search_by_name(
        self,
        query: str,
        *,
        limit: int,
        only_active: bool,
    ) -> list[SearchHit]:
        """`GET /companies/search` → `[SearchHit]`."""
        payload = await self._get_json(
            "/companies/search",
            params={
                "q": query,
                "limit": limit,
                "only_active": _bool_query(only_active),
            },
        )
        hits_raw = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(hits_raw, list):
            raise SourceUnavailableError(
                "Hosted API вернул неожиданный формат search-ответа.",
                details={"expected": "hits: list", "received": type(hits_raw).__name__},
            )
        return [SearchHit.model_validate(item) for item in hits_raw]

    async def get_full_card(
        self,
        *,
        inn: str | None,
        ogrn: str | None,
    ) -> CompanyCard | IECard:
        """`GET /companies/card?inn=...|ogrn=...` → `CompanyCard | IECard`.

        Контракт SPEC §4.1: если переданы оба — используется `inn`.
        """
        if inn is not None:
            params: dict[str, str] = {"inn": inn}
        elif ogrn is not None:
            params = {"ogrn": ogrn}
        else:
            raise ValidationError(
                "get_full_card требует хотя бы один из параметров: inn или ogrn.",
                hint="Передайте ИНН (10 или 12 цифр) или ОГРН (13 или 15 цифр).",
                details={"inn": inn, "ogrn": ogrn},
            )
        payload = await self._get_json("/companies/card", params=params)
        return self._parse_card(payload)

    async def get_founders(self, inn: str) -> list[Founder]:
        """`GET /companies/{inn}/founders` → `[Founder]`."""
        payload = await self._get_json(f"/companies/{inn}/founders")
        founders_raw = (
            payload.get("founders") if isinstance(payload, dict) else None
        )
        if not isinstance(founders_raw, list):
            raise SourceUnavailableError(
                "Hosted API вернул неожиданный формат founders-ответа.",
                details={
                    "expected": "founders: list",
                    "received": type(founders_raw).__name__,
                },
            )
        return [Founder.model_validate(item) for item in founders_raw]

    async def get_director(self, inn: str) -> Director | None:
        """`GET /companies/{inn}/director` → `Director | null`."""
        payload = await self._get_json(f"/companies/{inn}/director")
        director_raw = (
            payload.get("director") if isinstance(payload, dict) else None
        )
        if director_raw is None:
            return None
        return Director.model_validate(director_raw)

    async def bulk_cards(self, inns: list[str]) -> BulkResult:
        """`POST /companies/bulk` → `BulkResult`.

        Клиентская валидация размера батча идентична локальной версии
        (`BULK_CARDS_MAX_INNS`) — чтобы сэкономить round-trip, если
        пользователь явно перебрал лимит.
        """
        if not isinstance(inns, list):
            raise ValidationError(
                f"bulk_cards требует список ИНН, получено: {type(inns).__name__}.",
                details={"input_type": type(inns).__name__},
            )
        if len(inns) == 0:
            raise ValidationError(
                "bulk_cards: список ИНН пуст.",
                hint=f"Передайте 1..{BULK_CARDS_MAX_INNS} ИНН.",
                details={"count": 0},
            )
        if len(inns) > BULK_CARDS_MAX_INNS:
            raise BulkTooLargeError(
                (
                    f"bulk_cards: запрошено {len(inns)} ИНН, максимум "
                    f"{BULK_CARDS_MAX_INNS}."
                ),
                hint=(
                    f"Разбейте на батчи по {BULK_CARDS_MAX_INNS} ИНН."
                ),
                details={"count": len(inns), "max": BULK_CARDS_MAX_INNS},
            )
        payload = await self._post_json("/companies/bulk", json={"inns": inns})
        try:
            return BulkResult.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailableError(
                "Hosted API вернул невалидный BulkResult.",
                details={"cause": str(exc)},
            ) from exc

    # -----------------------------------------------------------------
    # Internals.
    # -----------------------------------------------------------------

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise SourceUnavailableError(
                f"Hosted API timeout при GET {path}.",
                hint="Проверьте сеть или увеличьте MCP_EGRUL_HTTP_TIMEOUT.",
                details={"cause": "timeout", "path": path},
            ) from exc
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(
                f"Hosted API недоступен при GET {path}: {exc!s}.",
                details={"cause": exc.__class__.__name__, "path": path},
            ) from exc
        return self._handle_response(response, method="GET", path=path)

    async def _post_json(
        self,
        path: str,
        *,
        json: dict[str, Any],
    ) -> Any:
        try:
            response = await self._client.post(path, json=json)
        except httpx.TimeoutException as exc:
            raise SourceUnavailableError(
                f"Hosted API timeout при POST {path}.",
                hint="Проверьте сеть или увеличьте MCP_EGRUL_HTTP_TIMEOUT.",
                details={"cause": "timeout", "path": path},
            ) from exc
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(
                f"Hosted API недоступен при POST {path}: {exc!s}.",
                details={"cause": exc.__class__.__name__, "path": path},
            ) from exc
        return self._handle_response(response, method="POST", path=path)

    def _handle_response(
        self,
        response: httpx.Response,
        *,
        method: str,
        path: str,
    ) -> Any:
        status = response.status_code
        if status == _HTTP_AUTH:
            raise HostedAuthError(
                "Hosted API отверг запрос: невалидный или отсутствующий ATOMNO_API_KEY.",
                hint="Проверьте ключ в переменной ATOMNO_API_KEY.",
                details=_error_details(response, method, path),
            )
        if status == _HTTP_FORBIDDEN:
            raise ProRequiredError(
                "Hosted API: требуется Pro-подписка для этой операции.",
                hint=(
                    "Free-ключ покрывает только базовые запросы. "
                    "Для bulk / истории / director-FIO нужен Pro."
                ),
                details=_error_details(response, method, path),
            )
        if status == _HTTP_NOT_FOUND:
            payload = _safe_json(response)
            code = (
                payload.get("code")
                if isinstance(payload, dict)
                else None
            )
            if code == "not_found":
                raise NotFoundError(
                    _server_message(payload)
                    or "Hosted API: запись не найдена.",
                    details=_error_details(response, method, path),
                )
            raise SourceUnavailableError(
                "Hosted API: маршрут не найден (возможно, сервер устарел).",
                details=_error_details(response, method, path),
            )
        if status == _HTTP_BULK_TOO_LARGE:
            raise BulkTooLargeError(
                "Hosted API отверг bulk-запрос как слишком большой.",
                details=_error_details(response, method, path),
            )
        if status == _HTTP_RATE_LIMIT:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitedError(
                "Hosted API: превышен rate-limit.",
                hint=(
                    f"Повторите через {retry_after} секунд."
                    if retry_after
                    else "Повторите позже или увеличьте tier подписки."
                ),
                details={
                    **_error_details(response, method, path),
                    "retry_after_seconds": retry_after,
                },
            )
        if _HTTP_4XX_LOW <= status < _HTTP_5XX_LOW:
            payload = _safe_json(response)
            raise ValidationError(
                _server_message(payload)
                or f"Hosted API вернул HTTP {status} при {method} {path}.",
                details=_error_details(response, method, path),
            )
        if status >= _HTTP_5XX_LOW:
            raise SourceUnavailableError(
                f"Hosted API вернул HTTP {status} при {method} {path}.",
                details={**_error_details(response, method, path), "cause": "server_error"},
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SourceUnavailableError(
                f"Hosted API вернул non-JSON ответ при {method} {path}.",
                details=_error_details(response, method, path),
            ) from exc

    @staticmethod
    def _parse_card(payload: Any) -> CompanyCard | IECard:
        """Разбор успешного ответа: `CompanyCard` или `IECard`.

        Ориентируемся на наличие `ogrnip` (уникальное поле ИП в SPEC §4.2)
        — если есть, это `IECard`, иначе `CompanyCard`.
        """
        if not isinstance(payload, dict):
            raise SourceUnavailableError(
                "Hosted API вернул неожиданный тип payload для карточки.",
                details={"received": type(payload).__name__},
            )
        try:
            if "ogrnip" in payload:
                return IECard.model_validate(payload)
            return CompanyCard.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise SourceUnavailableError(
                "Hosted API вернул невалидную карточку (pydantic validation).",
                details={"cause": str(exc)},
            ) from exc


def _bool_query(value: bool) -> str:
    """Контракт §5.4.1: bool в query-string как `true`/`false`."""
    return "true" if value else "false"


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _server_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        msg = payload.get("message")
        if isinstance(msg, str):
            return msg
    return None


def _error_details(
    response: httpx.Response,
    method: str,
    path: str,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "http_status": response.status_code,
    }


__all__ = ["HostedClient"]
