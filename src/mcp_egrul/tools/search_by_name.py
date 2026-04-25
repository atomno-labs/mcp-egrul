"""Тул `search_by_name`: fuzzy-поиск юр.лиц через FTS5.

Ограничения:
    * `query` короче `SEARCH_BY_NAME_MIN_QUERY_LENGTH` → `ValidationError`.
    * `limit` > `SEARCH_BY_NAME_MAX_LIMIT` → `ValidationError`.
    * `limit` <= 0 → `ValidationError`.

Пустой результат — валидный ответ `[]`, не ошибка.
"""

from __future__ import annotations

from ..constants import (
    SEARCH_BY_NAME_DEFAULT_LIMIT,
    SEARCH_BY_NAME_MAX_LIMIT,
    SEARCH_BY_NAME_MIN_QUERY_LENGTH,
)
from ..context import ServiceContext
from ..errors import ValidationError
from ..schemas import SearchHit


async def search_by_name(
    ctx: ServiceContext,
    query: str,
    *,
    limit: int = SEARCH_BY_NAME_DEFAULT_LIMIT,
    only_active: bool = False,
) -> list[SearchHit]:
    if not isinstance(query, str) or len(query.strip()) < SEARCH_BY_NAME_MIN_QUERY_LENGTH:
        raise ValidationError(
            (
                f"Слишком короткий поисковый запрос: '{query}'. "
                f"Минимум {SEARCH_BY_NAME_MIN_QUERY_LENGTH} символа."
            ),
            details={
                "query": query,
                "min_length": SEARCH_BY_NAME_MIN_QUERY_LENGTH,
            },
        )
    if not isinstance(limit, int) or limit <= 0:
        raise ValidationError(
            f"limit должен быть положительным целым, получено: {limit!r}.",
            details={"limit": limit},
        )
    if limit > SEARCH_BY_NAME_MAX_LIMIT:
        raise ValidationError(
            (
                f"limit={limit} превышает максимум "
                f"SEARCH_BY_NAME_MAX_LIMIT={SEARCH_BY_NAME_MAX_LIMIT}."
            ),
            hint=f"Передавайте limit ≤ {SEARCH_BY_NAME_MAX_LIMIT}.",
            details={"limit": limit, "max": SEARCH_BY_NAME_MAX_LIMIT},
        )

    normalized_query = query.strip()
    if ctx.hosted_client is not None:
        return await ctx.hosted_client.search_by_name(
            normalized_query,
            limit=limit,
            only_active=only_active,
        )

    rows = await ctx.store.search_companies_by_name(
        normalized_query,
        limit=limit,
        only_active=only_active,
    )
    return [SearchHit.model_validate(row) for row in rows]
