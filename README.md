# mcp-egrul

> MCP-сервер (Model Context Protocol — открытый протокол подключения AI-ассистентов к внешним инструментам) для работы с ЕГРЮЛ (Единый Государственный Реестр Юридических Лиц РФ) и ЕГРИП (Единый Государственный Реестр Индивидуальных Предпринимателей). Источник — официальные open-data дампы ФНС (Федеральной налоговой службы).

**Статус:** `v0.1.2` — open-версия (self-host через SQLite) полностью готова + клиентская часть hosted Pro (HTTP-клиент `HostedClient` для `api.atomno.ru`). Опубликована на [PyPI](https://pypi.org/project/atomno-mcp-egrul/), индексирована в [Glama](https://glama.ai/mcp/servers) и [Smithery](https://smithery.ai/). Сама hosted Pro-инфра — в активной разработке. **Coverage `100.00%`** (345 тестов, ruff clean, fastmcp 3.2.4, enforced через `--cov-fail-under=100`).

**Парный проект:** [`mcp-fns-check`](https://github.com/atomno-labs/mcp-fns-check) (risk-чек-слой поверх ЕГРЮЛ).

---

## Что это

Семь MCP-тулзов, видимых AI-ассистенту (Cursor, Claude Desktop, Cline, любой MCP-клиент):

| Tool | Описание | Аргументы |
|---|---|---|
| `search_by_inn` | Поиск по ИНН (10 цифр — юр.лицо, 12 — ИП) | `inn: str` |
| `search_by_ogrn` | Поиск по ОГРН (13) или ОГРНИП (15) | `ogrn: str` |
| `search_by_name` | Fuzzy-поиск по названию (FTS5) | `query: str, limit?: int, only_active?: bool` |
| `get_full_card` | Полная карточка со всеми секциями | `inn?: str, ogrn?: str` |
| `get_founders` | Только учредители с долями | `inn: str` |
| `get_director` | Только текущий руководитель | `inn: str` |
| `bulk_cards` | Массовая проверка (до 100 ИНН) | `inns: list[str]` |

Плюс диагностический `ping` для проверки что сервер жив.

Полная спецификация payload'ов — в `src/mcp_egrul/schemas.py` (Pydantic-модели `CompanyCard`, `IECard`, `SearchResult`, `BulkResult`).

---

## Установка

### Вариант 1 — через PyPI (рекомендуется для пользователей)

```bash
# Без локального clone — работает «из коробки»
uvx atomno-mcp-egrul

# Или установка глобально
pipx install atomno-mcp-egrul
atomno-mcp-egrul

# Или классический pip в venv
pip install atomno-mcp-egrul
atomno-mcp-egrul
```

### Вариант 2 — dev-режим (для разработчиков)

Требуется Python 3.11+ и [`uv`](https://docs.astral.sh/uv/) (быстрая замена pip, опционально).

```bash
git clone https://github.com/atomno-labs/mcp-egrul
cd mcp-egrul
uv venv
uv pip install -e ".[dev]"
```

Альтернативно через pip:

```bash
python -m venv .venv
.venv/Scripts/activate    # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"
```

---

## Запуск

```bash
atomno-mcp-egrul
```

Транспорт по умолчанию — **stdio** (стандартный ввод/вывод JSON-RPC). Подходит для подключения к Cursor / Claude Desktop / Claude Code.

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "egrul": {
      "command": "uvx",
      "args": ["atomno-mcp-egrul"]
    }
  }
}
```

### Cursor (`.cursor/mcp.json` в проекте или `~/.cursor/mcp.json` глобально)

```json
{
  "mcpServers": {
    "egrul": {
      "command": "uvx",
      "args": ["atomno-mcp-egrul"]
    }
  }
}
```

> Если не используете `uv`, замените `"command": "uvx", "args": ["atomno-mcp-egrul"]` на `"command": "atomno-mcp-egrul"` (требует `pip install atomno-mcp-egrul` или `pipx install atomno-mcp-egrul`).

---

## Docker (self-host) — quick start

```bash
# 1. Скачайте дампы ФНС (acceptance на сайте ФНС — раз в жизни).
#    Источники:
#      ЕГРЮЛ — https://www.nalog.gov.ru/opendata/7707329152-egrul/
#      ЕГРИП — https://www.nalog.gov.ru/opendata/7707329152-egrip/
#    Положите их в структуру:
mkdir -p dumps/egrul/2026-04-24 dumps/egrip/2026-04-24
cp ~/Downloads/EGRUL_*.zip dumps/egrul/2026-04-24/
cp ~/Downloads/EGRIP_*.zip dumps/egrip/2026-04-24/

# 2. Первоначальный полный импорт (однократно, ~30-60 минут):
docker compose --profile import run --rm \
    mcp-egrul-import atomno-mcp-egrul-import --registry egrul --full
docker compose --profile import run --rm \
    mcp-egrul-import atomno-mcp-egrul-import --registry egrip --full

# 3. Запустите сервер + фоновый cron-демон:
docker compose up -d
docker compose logs -f mcp-egrul-scheduler
```

Через ~10 минут после импорта все тулзы (`search_by_inn`, `search_by_name` и пр.) уже отвечают данными из локального слепка ФНС.

Схема тома `/data` внутри контейнера:

```
/data/
├── mcp_egrul_data.sqlite     # SQLite + FTS5
└── dumps/                    # read-only монтируется из ./dumps
    ├── egrul/
    │   └── YYYY-MM-DD/*.zip
    └── egrip/
        └── YYYY-MM-DD/*.zip
```

Cron-демон (`atomno-mcp-egrul-scheduler`) сам забирает самую свежую выгрузку после
того как вы положите её в `dumps/<registry>/<YYYY-MM-DD>/` — ночью в 03:00
Europe/Moscow. Если ничего нового нет — job завершится с `nothing_to_import`
и никаких лишних записей в `import_log` не сделает.

---

## Импорт дампов ФНС (ручной режим)

Источники:

- **ЕГРЮЛ open-data:** `https://www.nalog.gov.ru/opendata/7707329152-egrul/`
- **ЕГРИП open-data:** `https://www.nalog.gov.ru/opendata/7707329152-egrip/`

Формат: суточные архивы XML в ZIP, ~15 ГБ на полный слепок. Юридически их
нужно скачать **с сайта ФНС после acceptance лицензии** — сервер не качает
архивы сам (строго).

CLI:

```bash
# Полный первоначальный импорт (однократно):
atomno-mcp-egrul-import --registry egrul --full
atomno-mcp-egrul-import --registry egrip --full

# Инкремент (cron / ручной): загружается только если появилась более
# свежая YYYY-MM-DD-папка, чем последний успешный `import_log.source_dump_date`.
# Если новее нет — exit-code 5 и сообщение `nothing_to_import`.
atomno-mcp-egrul-import --registry egrul --incremental

# Фоновой cron-демон с ежедневным 03:00 MSK (вызывать вручную редко;
# обычно запускается сервисом mcp-egrul-scheduler в docker-compose).
atomno-mcp-egrul-scheduler --run-now
```

Exit-коды `atomno-mcp-egrul-import`:

| Код | Значение |
|---|---|
| 0 | Импорт прошёл успешно |
| 2 | Невалидный конфиг / аргумент CLI |
| 4 | Ошибка ингеста (битый XML, нет каталога дампов, DB error) |
| 5 | `nothing_to_import` — самая свежая дата уже в БД (инкремент) |

---

## Pro / hosted-режим (прокси на `api.atomno.ru`)

Когда пользователь задаёт `ATOMNO_API_KEY`, **все семь тулзов** автоматически
проксируются на hosted Pro API (SPEC §5.4, §5.4.1). Локальный SQLite в этом
режиме не используется — hosted Pro даёт:

- **Актуальные данные на сегодня** (без суточной задержки open-data дампа): прямой scrape `egrul.nalog.ru` + Dadata fallback на стороне сервера.
- **Bulk-эндпойнт без rate-limit** (`POST /companies/bulk`) — один запрос вместо N локальных gather'ов.
- **AI-summary карточки**, история изменений, поиск по ФИО директора (Pro-only тулзы — приезжают вместе с hosted-сервером в Phase 2, см. §5.4.1).

**Цена**: Pro — $10/мес отдельно или $15/мес в паре с `mcp-fns-check` (bundle-ключ). Free tier: 30 запросов/день/IP без регистрации (SPEC §1).

**Настройка в Cursor** (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "egrul": {
      "command": "uvx",
      "args": ["atomno-mcp-egrul"],
      "env": {
        "ATOMNO_API_KEY": "your-pro-key-here"
      }
    }
  }
}
```

**Поведение и ошибки** — никакого silent fallback: если hosted API недоступен, клиент поднимает типизированное исключение, а не молча отдаёт данные из устаревшего локального дампа. Сопоставление HTTP ↔ MCP-код ошибки — в SPEC §5.4.1:

| HTTP-ответ hosted API | Исключение клиента | `error.code` |
|---|---|---|
| 200 | — | — |
| 400 | `ValidationError` | `invalid_input` |
| 401 | `HostedAuthError` | `auth_required` |
| 403 | `ProRequiredError` | `pro_required` |
| 404 (code=not_found) | `NotFoundError` | `not_found` |
| 404 (wrong route) | `SourceUnavailableError` | `source_unavailable` |
| 413 | `BulkTooLargeError` | `bulk_too_large` |
| 429 | `RateLimitedError` (+ `Retry-After`) | `rate_limit` |
| 5xx | `SourceUnavailableError` | `source_unavailable` |
| timeout / DNS fail | `SourceUnavailableError` (cause=`timeout`/`ConnectError`) | `source_unavailable` |

Валидация ИНН/ОГРН остаётся **клиент-саид** (контрольные цифры проверяются до HTTP-запроса — экономия round-trip на битых идентификаторах).

---

## Конфигурация (переменные окружения)

| Переменная | Описание | По умолчанию |
|---|---|---|
| `MCP_EGRUL_DB` | Путь к SQLite-файлу со слепком ЕГРЮЛ/ЕГРИП | `./mcp_egrul_data.sqlite` |
| `MCP_EGRUL_USER_AGENT` | User-Agent HTTP-клиента | `mcp-egrul/0.1 (+https://github.com/atomno-labs/mcp-egrul)` |
| `MCP_EGRUL_HTTP_TIMEOUT` | Таймаут HTTP в секундах | `30` |
| `MCP_EGRUL_DUMPS_DIR` | Каталог с дампами ФНС, структура `<dir>/<registry>/<YYYY-MM-DD>/*.zip` | `./dumps` |
| `MCP_EGRUL_LOG_LEVEL` | Уровень логирования | `INFO` |
| `TZ` | Таймзона для scheduler (cron 03:00) | `Europe/Moscow` |
| `ATOMNO_API_KEY` | (Pro) ключ hosted-подписки — включает проксирование на `api.atomno.ru` | не задан |
| `ATOMNO_API_BASE` | (Pro) базовый URL hosted-API | `https://api.atomno.ru/mcp-egrul/v1` |

Пример — см. `.env.example`.

---

## Структура

```
apps/mcp-egrul/
├── pyproject.toml
├── LICENSE                             # MIT
├── README.md                           # ЭТОТ ФАЙЛ
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── src/mcp_egrul/
│   ├── __init__.py
│   ├── server.py                       # FastMCP entrypoint, регистрация 7 тулзов + ping
│   ├── context.py                      # ServiceContext (DI: SQLiteStore + HTTP-клиент)
│   ├── config.py                       # Чтение env-vars в типизированные поля
│   ├── constants.py                    # Все магические числа и enum'ы
│   ├── validators.py                   # Контрольные цифры ИНН (10/12) и ОГРН (13/15)
│   ├── schemas.py                      # Pydantic-модели CompanyCard/IECard/SearchResult/...
│   ├── errors.py                       # McpEgrulError и подклассы
│   ├── db/
│   │   ├── __init__.py
│   │   └── sqlite.py                   # Async-клиент (aiosqlite), init/query/upsert/search + import_log
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py                     # Абстрактный интерфейс Source
│   │   ├── opendata.py                 # ФНС open-data адаптер (read-local → SQLite upsert)
│   │   ├── opendata_parser.py          # Потоковый lxml.iterparse парсер ЕГРЮЛ/ЕГРИП XML
│   │   └── hosted_adapter.py           # HTTP-клиент hosted Pro API (SPEC §5.4.1)
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── search_by_inn.py
│   │   ├── search_by_ogrn.py
│   │   ├── search_by_name.py
│   │   ├── get_full_card.py
│   │   ├── get_founders.py
│   │   ├── get_director.py
│   │   └── bulk_cards.py
│   └── scripts/
│       ├── __init__.py
│       ├── import_opendata.py          # CLI `atomno-mcp-egrul-import` (ручной / одноразовый)
│       └── scheduler.py                # CLI `atomno-mcp-egrul-scheduler` (apscheduler cron 03:00 MSK)
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── fixtures/
    │   ├── egrul_sample.xml            # Мини-ЕГРЮЛ (2 валидных + 1 skip на неизвестный статус)
    │   └── egrip_sample.xml            # Мини-ЕГРИП (active + closed)
    ├── test_validators.py
    ├── test_schemas.py
    ├── test_config.py                  # Config.from_env + _parse_float_env (валидация env)
    ├── test_sqlite_store.py
    ├── test_cards.py                   # _cards.py: parse_iso_date/datetime + build_*card
    ├── test_server_ping.py             # FastMCP tool-layer + server.main()
    ├── test_tools.py                   # 7 тулзов: happy-path + validation + not_found
    ├── test_opendata_parser.py         # XML-парсер (zip, xml, skip-на-неизвестный-статус)
    ├── test_opendata_source.py         # OpenDataSource.run_ingest (full/incremental)
    ├── test_integration_import.py      # Полный цикл import → search → get_card
    ├── test_import_cli.py              # CLI `atomno-mcp-egrul-import`
    ├── test_scheduler_cli.py           # CLI `atomno-mcp-egrul-scheduler` + _run_scheduler
    └── test_hosted_adapter.py          # HostedClient + маршрутизация тулзов (respx-моки)
```

---

## Тесты

```bash
pytest -v --cov=src/mcp_egrul
```

Текущий coverage: **100.00%** (`345 tests passed`, ruff clean, 1529 statements + 382 branches,
**0 misses**). Enforced политикой `--cov-fail-under=100` — любая регрессия сломает CI. Тесты покрывают:

* валидаторы ИНН/ОГРН/ОГРНИП (контрольные цифры);
* `Config.from_env` + парсер float-env-переменных (валидация, а не silent fallback);
* все 7 MCP-тулзов (happy-path + validation + not_found + bulk partial);
* SQLite store + FTS5 + `import_log`;
* XML-парсер ЕГРЮЛ/ЕГРИП (zip, xml, skip-запись с неизвестным статусом);
* `OpenDataSource.run_ingest` (full/incremental/`nothing_to_import`);
* полный интеграционный цикл `import fixture → search → get_card → bulk`;
* обе CLI (`atomno-mcp-egrul-import`, `atomno-mcp-egrul-scheduler`) — регистрация cron-job'ов, парсинг
  аргументов, `_run_daily_ingest` на all-happy/`nothing_to_import`/`McpEgrulError`, полный цикл
  `_run_scheduler` с mock-ed `asyncio.Event`;
* FastMCP tool-layer через `mcp.call_tool()` — сериализация ошибок в структурированные dict'ы,
  `server.main()` с валидным и невалидным env;
* `HostedClient` (hosted Pro API proxy) — happy-path всех 7 методов, все HTTP-ошибки из
  SPEC §5.4.1 (401/403/404/413/429/5xx), timeout/ConnectError, невалидный JSON/payload от
  сервера, клиентская валидация bulk, `async with`-контекст; плюс маршрутизация из тулзов
  в hosted-режиме (при задан `ATOMNO_API_KEY` — запрос идёт в `api.atomno.ru`, не в SQLite,
  валидация ИНН до HTTP);
* edge-case'ы XML-парсера (75 отдельных unit-тестов на `_parse_company`/`_parse_ie`/
  `_parse_share`/`_parse_director`/`_parse_founders`/address fallback'ы/legacy-атрибуты/
  невалидные длины ИНН/ОГРН/КПП);
* приватные helper'ы SQLite-стора (`_wrap`, `_prepare_row`, `_row_to_dict`, `_normalize_bm25`,
  auto-init через `_ensure`, rejecting invalid `finish_import` статусов);
* `ServiceContext` reentry-идемпотентность, `atexit`-cleanup, `Config.from_env` ValidationError
  → exit-code 2 из `atomno-mcp-egrul-import` CLI.

Внешние API **никогда не вызываются напрямую** из тестов — только через `respx` (HTTP-мокинг) и
локальные XML-фикстуры (`tests/fixtures/`).

---

## Безопасность и юридический статус

- Все источники — **публично открытые данные ФНС** (ЕГРЮЛ / ЕГРИП open-datasets), распространение которых разрешено ФЗ «Об информации…» и ЕГРЮЛ-специфичными нормами (см. SPEC §8).
- Юридические лица не подпадают под 152-ФЗ (О персональных данных).
- ФИО физлиц-руководителей и учредителей публикуются самой ФНС в открытом реестре — пересылка этих данных легальна.
- Никаких write-операций ни в один внешний API.
- Секреты — только через переменные окружения, в репозитории — `.env.example` без значений.

---

## Дисклеймер

Сервис — **агрегатор и удобный интерфейс над публичными данными ФНС**. Не аффилирован с ФНС. Используется на ваш риск. Информация в ответах сервиса не является заменой полноценной юридической или финансовой оценки.

---

## Лицензия

MIT. Файл `LICENSE` в корне папки.
