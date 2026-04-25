"""Все магические числа и enum-строки пакета.

Никакие другие модули НЕ должны вводить числовые константы (длины, лимиты,
коды ошибок, TTL, имена таблиц) на своих уровнях — всё собрано здесь,
чтобы ревью было одноточечным и изменение поведения не размазывалось
по коду.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Форматы официальных идентификаторов (ФНС РФ).
# Источники — SPEC §0 (глоссарий) и §4.2.
# ---------------------------------------------------------------------------

INN_LEGAL_LENGTH: Final[int] = 10
INN_INDIVIDUAL_LENGTH: Final[int] = 12
OGRN_LEGAL_LENGTH: Final[int] = 13
OGRNIP_LENGTH: Final[int] = 15
KPP_LENGTH: Final[int] = 9

# ---------------------------------------------------------------------------
# Поиск.
# ---------------------------------------------------------------------------

SEARCH_BY_NAME_DEFAULT_LIMIT: Final[int] = 10
SEARCH_BY_NAME_MAX_LIMIT: Final[int] = 50
SEARCH_BY_NAME_MIN_QUERY_LENGTH: Final[int] = 2

BULK_CARDS_MAX_INNS: Final[int] = 100

# ---------------------------------------------------------------------------
# Статусы (строковые enum'ы; Literal-типы определены в schemas.py).
# ---------------------------------------------------------------------------

COMPANY_STATUSES: Final[tuple[str, ...]] = (
    "active",
    "reorganizing",
    "liquidating",
    "liquidated",
    "bankrupt",
)

IE_STATUSES: Final[tuple[str, ...]] = (
    "active",
    "closed",
)

DATA_SOURCES: Final[tuple[str, ...]] = (
    "opendata",
    "egrul-scrape",
    "dadata",
    "hosted",
)

FOUNDER_TYPES: Final[tuple[str, ...]] = (
    "person",
    "legal",
)

# ---------------------------------------------------------------------------
# Коды ошибок (стабильный контракт для AI-клиентов).
# ---------------------------------------------------------------------------

ERROR_CODE_VALIDATION: Final[str] = "invalid_input"
ERROR_CODE_NOT_FOUND: Final[str] = "not_found"
ERROR_CODE_SOURCE_UNAVAILABLE: Final[str] = "source_unavailable"
ERROR_CODE_RATE_LIMIT: Final[str] = "rate_limit"
ERROR_CODE_INTERNAL: Final[str] = "internal"
ERROR_CODE_NOT_IMPLEMENTED: Final[str] = "not_implemented"
ERROR_CODE_BULK_TOO_LARGE: Final[str] = "bulk_too_large"
ERROR_CODE_AUTH_REQUIRED: Final[str] = "auth_required"
ERROR_CODE_PRO_REQUIRED: Final[str] = "pro_required"

# ---------------------------------------------------------------------------
# SQLite.
# ---------------------------------------------------------------------------

TABLE_COMPANIES: Final[str] = "companies"
TABLE_IE: Final[str] = "individual_entrepreneurs"
TABLE_COMPANIES_FTS: Final[str] = "companies_fts"
TABLE_IE_FTS: Final[str] = "ie_fts"
TABLE_IMPORT_LOG: Final[str] = "import_log"

DEFAULT_DB_FILENAME: Final[str] = "mcp_egrul_data.sqlite"

# ---------------------------------------------------------------------------
# Импорт дампов ЕГРЮЛ/ЕГРИП.
# ---------------------------------------------------------------------------

IMPORT_UPSERT_BATCH_LOG_SIZE: Final[int] = 500
IMPORT_SUPPORTED_REGISTRIES: Final[tuple[str, ...]] = ("egrul", "egrip")
IMPORT_DATE_DIR_FORMAT: Final[str] = "%Y-%m-%d"
IMPORT_FNS_DATE_FORMAT: Final[str] = "%d.%m.%Y"

# ---------------------------------------------------------------------------
# Планировщик (apscheduler).
# ---------------------------------------------------------------------------

SCHEDULER_TIMEZONE: Final[str] = "Europe/Moscow"
SCHEDULER_CRON_HOUR: Final[int] = 3
SCHEDULER_CRON_MINUTE: Final[int] = 0
SCHEDULER_JOB_ID_EGRUL: Final[str] = "mcp-egrul-daily-egrul"
SCHEDULER_JOB_ID_EGRIP: Final[str] = "mcp-egrul-daily-egrip"
SCHEDULER_MISFIRE_GRACE_SECONDS: Final[int] = 3600

# ---------------------------------------------------------------------------
# HTTP-клиент.
# ---------------------------------------------------------------------------

DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
DEFAULT_USER_AGENT: Final[str] = (
    "mcp-egrul/0.1 (+https://github.com/atomno-labs/mcp-egrul)"
)

# ---------------------------------------------------------------------------
# Hosted proxy (Pro).
# ---------------------------------------------------------------------------

HOSTED_API_BASE_DEFAULT: Final[str] = "https://api.atomno.ru/mcp-egrul/v1"

# ---------------------------------------------------------------------------
# Имена env-переменных — в одном месте, чтобы не разъезжались между
# `config.py`, `.env.example` и Dockerfile.
# ---------------------------------------------------------------------------

ENV_DB_PATH: Final[str] = "MCP_EGRUL_DB"
ENV_USER_AGENT: Final[str] = "MCP_EGRUL_USER_AGENT"
ENV_HTTP_TIMEOUT: Final[str] = "MCP_EGRUL_HTTP_TIMEOUT"
ENV_DUMPS_DIR: Final[str] = "MCP_EGRUL_DUMPS_DIR"
ENV_LOG_LEVEL: Final[str] = "MCP_EGRUL_LOG_LEVEL"
ENV_HOSTED_API_KEY: Final[str] = "ATOMNO_API_KEY"
ENV_HOSTED_API_BASE: Final[str] = "ATOMNO_API_BASE"
