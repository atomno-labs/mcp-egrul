"""Async-реализации семи MCP-тулзов (SPEC §4.1).

Экспорт публичных функций с чистой сигнатурой — `server.py` оборачивает
каждую в MCP-декоратор и ловит `McpEgrulError` → `to_dict()`.
"""

from .bulk_cards import bulk_cards
from .get_director import get_director
from .get_founders import get_founders
from .get_full_card import get_full_card
from .search_by_inn import search_by_inn
from .search_by_name import search_by_name
from .search_by_ogrn import search_by_ogrn

__all__ = [
    "search_by_inn",
    "search_by_ogrn",
    "search_by_name",
    "get_full_card",
    "get_founders",
    "get_director",
    "bulk_cards",
]
