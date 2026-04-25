"""Тесты HostedClient (SPEC §5.4.1) + маршрутизации тулзов в hosted-режиме.

Покрывает:
    * Happy-path для всех 7 методов (search_by_inn, search_by_ogrn,
      search_by_name, get_full_card, get_founders, get_director,
      bulk_cards) — JSON от respx → pydantic-модели.
    * HTTP-ошибки: 401 → HostedAuthError, 403 → ProRequiredError,
      404 с `code=not_found` → NotFoundError, 404 без кода
      → SourceUnavailableError, 413 → BulkTooLargeError,
      429 → RateLimitedError, 5xx → SourceUnavailableError.
    * Timeout и сетевые ошибки → SourceUnavailableError.
    * Невалидный JSON от сервера → SourceUnavailableError.
    * Конструктор: пустой ключ / пустой base → ValidationError.
    * Клиентская валидация bulk (≤100, не-list, пустой) — до сети.
    * Маршрутизация из тулзов: `ctx.hosted_client is not None` →
      SQLite не трогается, запрос идёт через respx-мок.
    * Валидация в тулзах остаётся клиент-саид: битый ИНН роняет
      `ValidationError` до HTTP-запроса.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx

from mcp_egrul.config import Config
from mcp_egrul.context import ServiceContext
from mcp_egrul.db import SQLiteStore
from mcp_egrul.errors import (
    BulkTooLargeError,
    HostedAuthError,
    NotFoundError,
    ProRequiredError,
    RateLimitedError,
    SourceUnavailableError,
    ValidationError,
)
from mcp_egrul.schemas import CompanyCard, IECard
from mcp_egrul.sources.hosted_adapter import HostedClient
from mcp_egrul.tools.bulk_cards import bulk_cards
from mcp_egrul.tools.get_director import get_director
from mcp_egrul.tools.get_founders import get_founders
from mcp_egrul.tools.get_full_card import get_full_card
from mcp_egrul.tools.search_by_inn import search_by_inn
from mcp_egrul.tools.search_by_name import search_by_name
from mcp_egrul.tools.search_by_ogrn import search_by_ogrn

BASE_URL = "https://api.atomno.ru/mcp-egrul/v1"


# ---------------------------------------------------------------------------
# Фикстуры валидных JSON-ответов (совпадают со схемами из schemas.py).
# ---------------------------------------------------------------------------


def _company_json() -> dict[str, Any]:
    return {
        "inn": "7707083893",
        "ogrn": "1027700132195",
        "kpp": "773601001",
        "name_short": "ПАО СБЕРБАНК",
        "name_full": "ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО 'СБЕРБАНК РОССИИ'",
        "status": "active",
        "registered_at": "1991-03-20",
        "address_legal": "117997, г. Москва, ул. Вавилова, 19",
        "okved_main": {"code": "64.19", "description": "Денежное посредничество прочее"},
        "okved_additional": [],
        "director": {
            "fio": "Греф Г. О.",
            "position": "Президент",
        },
        "founders": [
            {
                "type": "legal",
                "name": "ЦБ РФ",
                "share_percent": 50.0,
            }
        ],
        "authorized_capital": 67760844000.0,
        "source": "hosted",
        "source_date": "2026-04-24",
        "fetched_at": datetime.now(tz=UTC).isoformat(),
    }


def _ie_json() -> dict[str, Any]:
    return {
        "ogrnip": "304500116000061",
        "inn": "500100732259",
        "fio": "Иванов Иван Иванович",
        "citizenship": "RU",
        "status": "active",
        "registered_at": "2004-01-15",
        "okved_main": {"code": "47.91.2", "description": "Розничная торговля"},
        "okved_additional": [],
        "source": "hosted",
        "source_date": "2026-04-24",
        "fetched_at": datetime.now(tz=UTC).isoformat(),
    }


def _search_hits_json() -> dict[str, Any]:
    return {
        "hits": [
            {
                "kind": "company",
                "inn": "7707083893",
                "ogrn": "1027700132195",
                "name": "ПАО СБЕРБАНК",
                "status": "active",
                "address_legal": "Москва, ул. Вавилова, 19",
                "relevance_score": 0.95,
            }
        ],
        "count": 1,
    }


def _founders_json() -> dict[str, Any]:
    return {
        "inn": "7707083893",
        "founders": [
            {
                "type": "legal",
                "name": "ЦБ РФ",
                "share_percent": 50.0,
            }
        ],
        "count": 1,
    }


def _director_json() -> dict[str, Any]:
    return {
        "inn": "7707083893",
        "director": {
            "fio": "Греф Г. О.",
            "position": "Президент",
        },
    }


def _bulk_json() -> dict[str, Any]:
    return {
        "cards": [_company_json()],
        "errors": [
            {"inn": "0000000000", "code": "not_found", "message": "записи нет"}
        ],
        "requested": 2,
        "found": 1,
    }


# ---------------------------------------------------------------------------
# Фикстуры клиента и контекста.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def hosted_client() -> HostedClient:
    client = HostedClient(
        api_base=BASE_URL,
        api_key="test-key-123",
        http_timeout_seconds=5.0,
        user_agent="mcp-egrul-test/0.1",
    )
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def hosted_config(tmp_path: Path) -> Config:
    """Config с hosted_mode_enabled=True (ключ задан)."""
    return Config(
        db_path=tmp_path / "test.sqlite",
        dumps_dir=tmp_path / "dumps",
        user_agent="mcp-egrul-test/0.1",
        http_timeout_seconds=5.0,
        log_level="INFO",
        hosted_api_key="test-key-123",
        hosted_api_base=BASE_URL,
    )


@pytest_asyncio.fixture
async def hosted_ctx(hosted_config: Config) -> ServiceContext:
    store = SQLiteStore(hosted_config.db_path)
    await store.init()
    hc = HostedClient(
        api_base=hosted_config.hosted_api_base,
        api_key=hosted_config.hosted_api_key or "",
        http_timeout_seconds=hosted_config.http_timeout_seconds,
        user_agent=hosted_config.user_agent,
    )
    ctx = ServiceContext.for_testing(
        store=store, config=hosted_config, hosted_client=hc
    )
    await ctx.__aenter__()
    try:
        yield ctx
    finally:
        await ctx.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Happy paths.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_inn_company_happy_path(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/inn/7707083893").respond(200, json=_company_json())
        card = await hosted_client.search_by_inn("7707083893")
    assert isinstance(card, CompanyCard)
    assert card.inn == "7707083893"
    assert card.source == "hosted"


@pytest.mark.asyncio
async def test_search_by_inn_ie_returns_iecard(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/inn/500100732259").respond(200, json=_ie_json())
        card = await hosted_client.search_by_inn("500100732259")
    assert isinstance(card, IECard)
    assert card.ogrnip == "304500116000061"


@pytest.mark.asyncio
async def test_search_by_ogrn_happy_path(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/ogrn/1027700132195").respond(200, json=_company_json())
        card = await hosted_client.search_by_ogrn("1027700132195")
    assert isinstance(card, CompanyCard)


@pytest.mark.asyncio
async def test_search_by_name_happy_path(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.get("/companies/search").respond(200, json=_search_hits_json())
        hits = await hosted_client.search_by_name(
            "Сбербанк", limit=5, only_active=True
        )
    assert len(hits) == 1
    assert hits[0].inn == "7707083893"
    # bool → 'true'/'false' (SPEC §5.4.1).
    request = route.calls[0].request
    assert "only_active=true" in str(request.url)
    assert "limit=5" in str(request.url)


@pytest.mark.asyncio
async def test_get_full_card_with_inn(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.get("/companies/card").respond(200, json=_company_json())
        card = await hosted_client.get_full_card(inn="7707083893", ogrn=None)
    assert isinstance(card, CompanyCard)
    assert "inn=7707083893" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_get_full_card_with_ogrn(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.get("/companies/card").respond(200, json=_company_json())
        card = await hosted_client.get_full_card(inn=None, ogrn="1027700132195")
    assert isinstance(card, CompanyCard)
    assert "ogrn=1027700132195" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_get_full_card_without_args(hosted_client: HostedClient) -> None:
    with pytest.raises(ValidationError):
        await hosted_client.get_full_card(inn=None, ogrn=None)


@pytest.mark.asyncio
async def test_get_founders_happy_path(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/7707083893/founders").respond(
            200, json=_founders_json()
        )
        founders = await hosted_client.get_founders("7707083893")
    assert len(founders) == 1
    assert founders[0].share_percent == 50.0


@pytest.mark.asyncio
async def test_get_director_happy_path(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/7707083893/director").respond(200, json=_director_json())
        director = await hosted_client.get_director("7707083893")
    assert director is not None
    assert director.fio == "Греф Г. О."


@pytest.mark.asyncio
async def test_get_director_null(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/7707083893/director").respond(
            200, json={"inn": "7707083893", "director": None}
        )
        director = await hosted_client.get_director("7707083893")
    assert director is None


@pytest.mark.asyncio
async def test_bulk_cards_happy_path(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.post("/companies/bulk").respond(200, json=_bulk_json())
        result = await hosted_client.bulk_cards(["7707083893", "0000000000"])
    assert result.requested == 2
    assert result.found == 1
    assert len(result.cards) == 1
    assert len(result.errors) == 1
    # POST-тело содержит все ИНН.
    body = route.calls[0].request.content.decode()
    assert "7707083893" in body
    assert "0000000000" in body


# ---------------------------------------------------------------------------
# HTTP-ошибки.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_401_raises_auth(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(
            401, json={"error": True, "code": "auth_required", "message": "bad key"}
        )
        with pytest.raises(HostedAuthError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_http_403_raises_pro_required(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/companies/bulk").respond(
            403, json={"error": True, "code": "pro_required", "message": "upgrade"}
        )
        with pytest.raises(ProRequiredError):
            await hosted_client.bulk_cards(["7707083893"])


@pytest.mark.asyncio
async def test_http_404_not_found_preserves_notfound(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(
            404,
            json={"error": True, "code": "not_found", "message": "записи нет"},
        )
        with pytest.raises(NotFoundError) as exc_info:
            await hosted_client.search_by_inn("7707083893")
    assert "записи нет" in exc_info.value.message_ru


@pytest.mark.asyncio
async def test_http_404_wrong_route_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(404, json={"error": True})
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_http_413_raises_bulk_too_large(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/companies/bulk").respond(
            413, json={"error": True, "code": "bulk_too_large", "message": "too many"}
        )
        with pytest.raises(BulkTooLargeError):
            await hosted_client.bulk_cards(["7707083893"])


@pytest.mark.asyncio
async def test_http_429_raises_rate_limit_with_retry_after(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(
            429,
            headers={"Retry-After": "60"},
            json={"error": True, "code": "rate_limit", "message": "slow down"},
        )
        with pytest.raises(RateLimitedError) as exc_info:
            await hosted_client.search_by_inn("7707083893")
    assert exc_info.value.details.get("retry_after_seconds") == "60"


@pytest.mark.asyncio
async def test_http_500_raises_source_unavailable(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(500, text="oops")
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_http_400_raises_validation_with_server_message(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/search").respond(
            400,
            json={
                "error": True,
                "code": "invalid_input",
                "message": "q must be >= 2 chars",
            },
        )
        with pytest.raises(ValidationError) as exc_info:
            await hosted_client.search_by_name("a", limit=5, only_active=False)
    assert "q must be >= 2 chars" in exc_info.value.message_ru


# ---------------------------------------------------------------------------
# Сетевые / JSON-ошибки.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_raises_source_unavailable(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").mock(
            side_effect=httpx.ReadTimeout("slow backend")
        )
        with pytest.raises(SourceUnavailableError) as exc_info:
            await hosted_client.search_by_inn("7707083893")
    assert exc_info.value.details.get("cause") == "timeout"


@pytest.mark.asyncio
async def test_connect_error_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").mock(
            side_effect=httpx.ConnectError("dns fail")
        )
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_non_json_200_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(200, text="<html>not json</html>")
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_invalid_card_payload_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(
            200, json={"inn": "7707083893"}  # без обязательных полей CompanyCard
        )
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_search_returns_non_list_hits_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/search").respond(200, json={"hits": "not-a-list"})
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_name("Сбер", limit=5, only_active=False)


@pytest.mark.asyncio
async def test_founders_returns_non_list_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/7707083893/founders").respond(
            200, json={"founders": None}
        )
        with pytest.raises(SourceUnavailableError):
            await hosted_client.get_founders("7707083893")


@pytest.mark.asyncio
async def test_bulk_invalid_payload_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/companies/bulk").respond(200, json={"weird": "shape"})
        with pytest.raises(SourceUnavailableError):
            await hosted_client.bulk_cards(["7707083893"])


# ---------------------------------------------------------------------------
# Конструктор и клиентская валидация.
# ---------------------------------------------------------------------------


def test_empty_api_key_raises_validation() -> None:
    with pytest.raises(ValidationError):
        HostedClient(
            api_base=BASE_URL,
            api_key="",
            http_timeout_seconds=5.0,
            user_agent="test",
        )


def test_empty_api_base_raises_validation() -> None:
    with pytest.raises(ValidationError):
        HostedClient(
            api_base="",
            api_key="key",
            http_timeout_seconds=5.0,
            user_agent="test",
        )


@pytest.mark.asyncio
async def test_bulk_client_side_validation_empty(hosted_client: HostedClient) -> None:
    with pytest.raises(ValidationError):
        await hosted_client.bulk_cards([])


@pytest.mark.asyncio
async def test_bulk_client_side_validation_not_list(
    hosted_client: HostedClient,
) -> None:
    with pytest.raises(ValidationError):
        await hosted_client.bulk_cards("not-a-list")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_bulk_client_side_validation_too_large(
    hosted_client: HostedClient,
) -> None:
    with pytest.raises(BulkTooLargeError):
        await hosted_client.bulk_cards(["7707083893"] * 101)


@pytest.mark.asyncio
async def test_bearer_header_in_request(hosted_client: HostedClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.get("/companies/inn/7707083893").respond(
            200, json=_company_json()
        )
        await hosted_client.search_by_inn("7707083893")
    assert (
        route.calls[0].request.headers.get("Authorization") == "Bearer test-key-123"
    )


@pytest.mark.asyncio
async def test_close_idempotent(hosted_client: HostedClient) -> None:
    await hosted_client.close()
    # второй close не должен упасть
    await hosted_client.close()


@pytest.mark.asyncio
async def test_post_timeout_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    """Таймаут в POST-ветке покрывает отдельную try/except."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/companies/bulk").mock(
            side_effect=httpx.ReadTimeout("slow backend")
        )
        with pytest.raises(SourceUnavailableError) as exc_info:
            await hosted_client.bulk_cards(["7707083893"])
    assert exc_info.value.details.get("cause") == "timeout"


@pytest.mark.asyncio
async def test_post_connect_error_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/companies/bulk").mock(
            side_effect=httpx.ConnectError("dns fail")
        )
        with pytest.raises(SourceUnavailableError):
            await hosted_client.bulk_cards(["7707083893"])


@pytest.mark.asyncio
async def test_parse_card_non_dict_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    """Сервер прислал list вместо карточки → SourceUnavailableError (не AttributeError)."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(200, json=["not", "a", "card"])
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_http_404_with_non_json_body_raises_source_unavailable(
    hosted_client: HostedClient,
) -> None:
    """404 с не-JSON телом — `_safe_json` возвращает None, не падает ValueError."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(404, text="plain 404")
        with pytest.raises(SourceUnavailableError):
            await hosted_client.search_by_inn("7707083893")


@pytest.mark.asyncio
async def test_http_400_with_non_string_message_field(
    hosted_client: HostedClient,
) -> None:
    """Сервер вернул `message: 123` — `_server_message` отдаёт None, падает на дефолт."""
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/search").respond(
            400,
            json={"error": True, "code": "invalid_input", "message": 12345},
        )
        with pytest.raises(ValidationError) as exc_info:
            await hosted_client.search_by_name("ab", limit=5, only_active=False)
    # Не взяли server-side message (не строка), используем наш дефолтный текст.
    assert "HTTP 400" in exc_info.value.message_ru


@pytest.mark.asyncio
async def test_http_400_with_list_body_server_message_returns_none() -> None:
    """Body: JSON-list вместо dict → `_server_message` → None, падает на дефолт."""
    client = HostedClient(
        api_base=BASE_URL,
        api_key="k",
        http_timeout_seconds=5.0,
        user_agent="t",
    )
    try:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/companies/search").respond(400, json=["not", "dict"])
            with pytest.raises(ValidationError) as exc_info:
                await client.search_by_name("ab", limit=5, only_active=False)
        assert "HTTP 400" in exc_info.value.message_ru
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_async_context_manager_enters_and_closes() -> None:
    """`async with HostedClient(...)` — открытие/закрытие на одной строке."""
    async with HostedClient(
        api_base=BASE_URL,
        api_key="k",
        http_timeout_seconds=5.0,
        user_agent="t",
    ) as client:
        assert isinstance(client, HostedClient)
    # После выхода из контекста — клиент закрыт, повторный close() не падает.
    await client.close()


# ---------------------------------------------------------------------------
# Маршрутизация из тулзов (hosted-режим).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_search_by_inn_uses_hosted(hosted_ctx: ServiceContext) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/inn/7707083893").respond(200, json=_company_json())
        card = await search_by_inn(hosted_ctx, "7707083893")
    assert isinstance(card, CompanyCard)
    # Локальный SQLite пуст — если бы fallback был, тест упал бы на NotFoundError.


@pytest.mark.asyncio
async def test_tool_search_by_inn_validation_before_hosted(
    hosted_ctx: ServiceContext,
) -> None:
    """Битый ИНН ловится клиент-саид, HTTP-запрос не уходит."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.get("/companies/inn/1111111111").respond(200, json=_company_json())
        with pytest.raises(ValidationError):
            await search_by_inn(hosted_ctx, "1111111111")
        assert route.call_count == 0


@pytest.mark.asyncio
async def test_tool_search_by_ogrn_uses_hosted(hosted_ctx: ServiceContext) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/ogrn/1027700132195").respond(200, json=_company_json())
        card = await search_by_ogrn(hosted_ctx, "1027700132195")
    assert isinstance(card, CompanyCard)


@pytest.mark.asyncio
async def test_tool_search_by_name_uses_hosted(hosted_ctx: ServiceContext) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/search").respond(200, json=_search_hits_json())
        hits = await search_by_name(hosted_ctx, "Сбер", limit=5, only_active=False)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_tool_get_full_card_uses_hosted_with_inn(
    hosted_ctx: ServiceContext,
) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.get("/companies/card").respond(200, json=_company_json())
        await get_full_card(hosted_ctx, inn="7707083893")
    assert "inn=7707083893" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_tool_get_full_card_uses_hosted_with_ogrn(
    hosted_ctx: ServiceContext,
) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.get("/companies/card").respond(200, json=_company_json())
        await get_full_card(hosted_ctx, ogrn="1027700132195")
    assert "ogrn=1027700132195" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_tool_get_full_card_no_args_raises(hosted_ctx: ServiceContext) -> None:
    with pytest.raises(ValidationError):
        await get_full_card(hosted_ctx)


@pytest.mark.asyncio
async def test_tool_get_founders_uses_hosted(hosted_ctx: ServiceContext) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/7707083893/founders").respond(
            200, json=_founders_json()
        )
        founders = await get_founders(hosted_ctx, "7707083893")
    assert len(founders) == 1


@pytest.mark.asyncio
async def test_tool_get_founders_rejects_ie_before_hosted(
    hosted_ctx: ServiceContext,
) -> None:
    """У ИП нет учредителей — ValidationError до HTTP."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.get("/companies/500100732259/founders").respond(
            200, json=_founders_json()
        )
        with pytest.raises(ValidationError):
            await get_founders(hosted_ctx, "500100732259")
        assert route.call_count == 0


@pytest.mark.asyncio
async def test_tool_get_director_uses_hosted(hosted_ctx: ServiceContext) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        mock.get("/companies/7707083893/director").respond(
            200, json=_director_json()
        )
        director = await get_director(hosted_ctx, "7707083893")
    assert director is not None


@pytest.mark.asyncio
async def test_tool_bulk_cards_uses_hosted_single_post(
    hosted_ctx: ServiceContext,
) -> None:
    """Bulk в hosted-режиме — один POST, а не N GET-ов."""
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock:
        route = mock.post("/companies/bulk").respond(200, json=_bulk_json())
        result = await bulk_cards(hosted_ctx, ["7707083893", "7728168971"])
    assert route.call_count == 1
    assert result.requested == 2


@pytest.mark.asyncio
async def test_tool_bulk_cards_validation_before_hosted(
    hosted_ctx: ServiceContext,
) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.post("/companies/bulk").respond(200, json=_bulk_json())
        with pytest.raises(BulkTooLargeError):
            await bulk_cards(hosted_ctx, ["7707083893"] * 101)
        assert route.call_count == 0


@pytest.mark.asyncio
async def test_tool_hosted_5xx_no_silent_fallback(hosted_ctx: ServiceContext) -> None:
    """Hosted 5xx НЕ должен тихо уйти в локальный SQLite."""
    # Заполним локальный SQLite, чтобы fallback был технически возможен.
    from tests.conftest import make_company_row

    await hosted_ctx.store.upsert_company(make_company_row())
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/companies/inn/7707083893").respond(500, text="boom")
        with pytest.raises(SourceUnavailableError):
            await search_by_inn(hosted_ctx, "7707083893")


# ---------------------------------------------------------------------------
# ServiceContext.from_config: HostedClient создаётся при hosted_mode_enabled.
# ---------------------------------------------------------------------------


def test_from_config_without_key_has_no_hosted_client(tmp_path: Path) -> None:
    config = Config(
        db_path=tmp_path / "db.sqlite",
        dumps_dir=tmp_path / "dumps",
        user_agent="test",
        http_timeout_seconds=5.0,
        log_level="INFO",
        hosted_api_key=None,
        hosted_api_base=BASE_URL,
    )
    ctx = ServiceContext.from_config(config)
    assert ctx.hosted_client is None


def test_from_config_with_key_creates_hosted_client(tmp_path: Path) -> None:
    config = Config(
        db_path=tmp_path / "db.sqlite",
        dumps_dir=tmp_path / "dumps",
        user_agent="test",
        http_timeout_seconds=5.0,
        log_level="INFO",
        hosted_api_key="abc",
        hosted_api_base=BASE_URL,
    )
    ctx = ServiceContext.from_config(config)
    assert isinstance(ctx.hosted_client, HostedClient)
