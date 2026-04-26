# Changelog

Все значимые изменения этого пакета отражаются в этом файле.

Формат основан на [Keep a Changelog 1.1.0](https://keepachangelog.com/ru/1.1.0/),
версионирование — [SemVer 2.0.0](https://semver.org/lang/ru/).

---

## [Unreleased]

### Запланировано

- Карточка в каталоге [programmatic-mcp-ru](https://github.com/atomno-labs/programmatic-mcp-ru).
- PR в `modelcontextprotocol/servers` после получения badge от Glama.
- PR в `punkpeye/awesome-mcp-servers` после получения badge от Glama.

---

## [0.1.2] — 2026-04-26

Брендовая унификация PyPI с парным проектом `atomno-mcp-fns-check`.
Старый PyPI-пакет `mcp-egrul==0.1.1` помечен `yanked`.

### Изменено (BREAKING для PyPI)

- **PyPI имя пакета**: `mcp-egrul` → **`atomno-mcp-egrul`**.
  Установка теперь: `uvx atomno-mcp-egrul` / `pipx install atomno-mcp-egrul` /
  `pip install atomno-mcp-egrul`.
- **CLI commands**:
  - `mcp-egrul` → `atomno-mcp-egrul`,
  - `mcp-egrul-import` → `atomno-mcp-egrul-import`,
  - `mcp-egrul-scheduler` → `atomno-mcp-egrul-scheduler`.
- **Dockerfile** `ENTRYPOINT` обновлён на `atomno-mcp-egrul`.
- **`docker-compose.yml`** commands обновлены (service names и
  `container_name` оставлены как `mcp-egrul-*` — это локальные алиасы).
- **`smithery.yaml`** обновлён: `args: ['atomno-mcp-egrul']`.
- **README**: все команды установки и snippets для Cursor / Claude Desktop /
  Claude Code приведены к новому имени.

### Не изменилось

- **Python module name** внутри пакета остаётся `mcp_egrul`
  (видно только в `from mcp_egrul.X import ...` внутри своего кода —
  публичный API не меняется).
- **GitHub repo** остаётся `atomno-labs/mcp-egrul` (org даёт брендирование).
- **FastMCP server name** в `initialize`/`ping` ответе остаётся `mcp-egrul`
  (стабильный контракт для существующих клиентов).
- **Логика, тулзы, схемы, БД** — без изменений (568 тестов проходят без
  правок логики, только обновлены docstrings).

### Миграция для пользователей `mcp-egrul==0.1.1`

```bash
pip uninstall mcp-egrul
pip install atomno-mcp-egrul
# в .cursor/mcp.json и claude_desktop_config.json:
# "command": "uvx", "args": ["atomno-mcp-egrul"]
```

---

## [0.1.1] — 2026-04-26 [YANKED]

> **Yanked**: имя пакета `mcp-egrul` помечено deprecated в пользу
> `atomno-mcp-egrul` (см. [0.1.2]). Команда `pip install mcp-egrul`
> без явного `==0.1.1` больше не сработает.

Catalog-патч. Основной код пакета не меняется — добавлены метаданные для
индексации в каталогах MCP-серверов.

### Добавлено

- **`smithery.yaml`** в корне репо — конфиг для Smithery.ai (stdio-режим,
  опциональный `ATOMNO_API_KEY` для Hosted Pro, выбор log-level).
- **`glama.json`** в корне репо — claim ownership на Glama.ai под org
  `atomno-labs`.
- **Первая публикация на PyPI** — `pip install mcp-egrul` или
  `uvx mcp-egrul` (yanked в 0.1.2).

### Изменено

- README дополнен секцией про установку через PyPI.

---

## [0.1.0] — 2026-04-25

Первый функционально полный релиз. Open-версия (self-host через SQLite) +
клиентская часть hosted Pro (HTTP-клиент `HostedClient`).

### Добавлено

- **MCP-сервер** на FastMCP 3.2.4 с 7 тулзами + `ping`:
  - `search_by_inn` (10/12 цифр → `CompanyCard` / `IECard`),
  - `search_by_ogrn` (13/15 цифр),
  - `search_by_name` (FTS5 по названию),
  - `get_full_card` (полная карточка по ИНН **или** ОГРН),
  - `get_founders` (учредители с долями),
  - `get_director` (текущий руководитель),
  - `bulk_cards` (до 100 ИНН за вызов).
- **Pydantic v2 schemas** для всех тулзов (`CompanyCard`, `IECard`,
  `SearchResult`, `BulkResult`).
- **Async SQLite store** (`aiosqlite`) с FTS5 по названиям и таблицей
  `import_log` (журнал успешных загрузок дампов).
- **OpenData-парсер ЕГРЮЛ/ЕГРИП** на `lxml.iterparse` (потоковый, поддерживает
  `.xml` и `.zip` архивы из открытых дампов ФНС).
- **CLI `mcp-egrul-import`** — однократный импорт (`--full` / `--incremental`)
  с exit-кодами по контракту (0 success / 2 user-error / 4 ingest-error /
  5 nothing-to-import).
- **CLI `mcp-egrul-scheduler`** — фоновой cron-демон через apscheduler
  (`03:00 Europe/Moscow`, `--run-now` для немедленного запуска).
- **`HostedClient`** (HTTP-клиент hosted Pro API на `httpx.AsyncClient`):
  - все 7 методов соответствуют контракту,
  - mapping HTTP → MCP-исключения (400→`ValidationError`, 401→`HostedAuthError`,
    403→`ProRequiredError`, 404→`NotFoundError`, 413→`BulkTooLargeError`,
    429→`RateLimitedError` (+ `Retry-After`), 5xx/timeout/ConnectError →
    `SourceUnavailableError`),
  - валидация ИНН/ОГРН на стороне клиента до HTTP-запроса.
- **Маршрутизация `ATOMNO_API_KEY`-aware**: при заданном ключе всё проксируется
  на hosted API, локальный SQLite не используется. Никакого silent fallback
  на устаревший локальный дамп при сбое hosted API.
- **Docker self-host**: `Dockerfile` (Python 3.12-slim) + `docker-compose.yml`
  с тремя сервисами (`mcp-egrul`, `mcp-egrul-scheduler`, опциональный
  `mcp-egrul-import`).
- **Документация**: README с примерами для Cursor / Claude Desktop / Claude Code,
  таблицей env-vars, полным списком тулзов, Docker quick-start.

### Качество

- **Coverage 100.00%** (1529 statements + 382 branches, 0 misses, 0 partial
  branches), enforced через `pyproject.toml: tool.coverage.report.fail_under=100`.
- **345 тестов** (`pytest -v`):
  - юнит-тесты: валидаторы, schemas, config, sqlite-store, парсер ЕГРЮЛ/ЕГРИП,
    7 тулзов, `HostedClient` (включая все HTTP-ошибки контракта), CLI обоих
    скриптов, FastMCP tool-layer;
  - интеграционные: полный цикл `import fixture XML → search → get_card → bulk`;
  - edge-case'ы парсера: 75 тестов на legacy-атрибуты, fallback'ы адресов, пустые
    секции, неизвестные статусы.
- **Никаких прямых обращений к внешним API в тестах** — только `respx` (HTTP)
  и локальные XML-фикстуры (`tests/fixtures/`).
- **`ruff` clean** (правила `E F I B UP ASYNC`, line-length 100).

### Безопасность

- Все источники — публично открытые данные ФНС (юр.лица не подпадают под 152-ФЗ).
- Никаких write-операций в внешние API.
- Секреты — только через env (`.env.example` без значений в репозитории).
- `.gitignore` исключает `.env`, `*.sqlite`, `dumps/`, `.coverage`, `htmlcov/`,
  IDE-папки.

### Лицензия

MIT (`LICENSE` в корне пакета).

[Unreleased]: https://github.com/atomno-labs/mcp-egrul/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/atomno-labs/mcp-egrul/releases/tag/v0.1.2
[0.1.1]: https://github.com/atomno-labs/mcp-egrul/releases/tag/v0.1.1
[0.1.0]: https://github.com/atomno-labs/mcp-egrul/releases/tag/v0.1.0
