"""Адаптеры источников данных для mcp-egrul.

Контракт `Source` (ingest-абстракция) фиксируется в `base.py`. Реализации:
    * `opendata.OpenDataSource` — официальные дампы ФНС (основа open-версии,
      Phase 1). `run_ingest()` читает локальные ZIP-архивы из
      `MCP_EGRUL_DUMPS_DIR/<registry>/<YYYY-MM-DD>/` и стримит их в SQLite
      через `opendata_parser.iter_dump_records`.
    * `hosted_adapter.HostedClient` — HTTP-прокси на hosted Pro API
      `api.atomno.ru/mcp-egrul/v1` (SPEC §5.4.1). НЕ `Source`, а отдельный
      клиент для тул-слоя: когда пользователь задал `ATOMNO_API_KEY`,
      запросы в MCP-тулзах идут через него (свежие данные, bulk без
      rate-limit), а не через локальный SQLite.

Принцип «no silent fallback» — если источник не может выполнить операцию,
он бросает `McpEgrulError` с типом (`NothingToImportError`,
`SourceUnavailableError`, `HostedAuthError`, `ProRequiredError`), и
CLI / scheduler / тулзы явно пробрасывают это дальше, а не тихо возвращают
пустой результат или молча деградируют в локальный режим.
"""

from .base import Source
from .hosted_adapter import HostedClient
from .opendata import OpenDataSource

__all__ = ["HostedClient", "OpenDataSource", "Source"]
