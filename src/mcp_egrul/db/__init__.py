"""SQLite-слой mcp-egrul.

Экспортирует `SQLiteStore` — единственную точку чтения/записи локального
слепка ЕГРЮЛ/ЕГРИП для open-версии. DDL-схема встроена в `sqlite.py` как
module-level константа `SCHEMA_SQL` и применяется при `init()`.
"""

from .sqlite import SCHEMA_SQL, SQLiteStore

__all__ = ["SQLiteStore", "SCHEMA_SQL"]
