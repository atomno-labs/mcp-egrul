"""ServiceContext: DI-контейнер пакета.

Держит единственный `SQLiteStore` + (опц.) `HostedClient` и параметры
конфигурации на весь жизненный цикл процесса. Создаётся лениво в
`server._get_ctx()` при первом вызове тулза.

Когда `config.hosted_mode_enabled` — `HostedClient` инициализируется из
env; MCP-тулзы маршрутизируют запросы в него (свежие данные, bulk без
rate-limit), а не в локальный SQLite.

Тесты используют `ServiceContext.for_testing(...)` с временным SQLite
в `tmp_path` и (опц.) подменённым `HostedClient`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config
from .db import SQLiteStore
from .sources import HostedClient


@dataclass
class ServiceContext:
    """Контекст сервиса: store + (опц.) hosted-client + конфигурация."""

    store: SQLiteStore
    config: Config
    hosted_client: HostedClient | None = None
    _entered: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_config(cls, config: Config) -> ServiceContext:
        hosted_client: HostedClient | None = None
        if config.hosted_mode_enabled:
            assert config.hosted_api_key is not None  # защищено hosted_mode_enabled
            hosted_client = HostedClient(
                api_base=config.hosted_api_base,
                api_key=config.hosted_api_key,
                http_timeout_seconds=config.http_timeout_seconds,
                user_agent=config.user_agent,
            )
        return cls(
            store=SQLiteStore(config.db_path),
            config=config,
            hosted_client=hosted_client,
        )

    @classmethod
    def from_env(cls) -> ServiceContext:
        return cls.from_config(Config.from_env())

    @classmethod
    def for_testing(
        cls,
        *,
        store: SQLiteStore,
        config: Config,
        hosted_client: HostedClient | None = None,
    ) -> ServiceContext:
        return cls(store=store, config=config, hosted_client=hosted_client)

    async def __aenter__(self) -> ServiceContext:
        if self._entered:
            return self
        await self.store.init()
        self._entered = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if not self._entered:
            return
        await self.store.close()
        if self.hosted_client is not None:
            await self.hosted_client.close()
        self._entered = False
