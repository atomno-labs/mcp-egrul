"""Чтение переменных окружения в типизированную структуру `Config`.

Единая точка, где пакет обращается к `os.environ`. Все остальные модули
работают через `Config` или через `ServiceContext` (см. context.py).

Ошибка парсинга числового env-значения — НЕ silent fallback на дефолт.
Мы поднимаем `ValidationError` с точным указанием переменной и значения,
чтобы `mcp-egrul` не стартовал с «молча исправленной» конфигурацией.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    DEFAULT_DB_FILENAME,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    ENV_DB_PATH,
    ENV_DUMPS_DIR,
    ENV_HOSTED_API_BASE,
    ENV_HOSTED_API_KEY,
    ENV_HTTP_TIMEOUT,
    ENV_LOG_LEVEL,
    ENV_USER_AGENT,
    HOSTED_API_BASE_DEFAULT,
)
from .errors import ValidationError


@dataclass(frozen=True)
class Config:
    """Типизированный снимок env на момент старта сервера."""

    db_path: Path
    dumps_dir: Path
    user_agent: str
    http_timeout_seconds: float
    log_level: str
    hosted_api_key: str | None
    hosted_api_base: str

    @property
    def hosted_mode_enabled(self) -> bool:
        """Если пользователь задал `ATOMNO_API_KEY` — проксируем в hosted."""
        return self.hosted_api_key is not None and self.hosted_api_key != ""

    @classmethod
    def from_env(cls) -> Config:
        cwd = Path.cwd()
        db_path = Path(os.environ.get(ENV_DB_PATH) or str(cwd / DEFAULT_DB_FILENAME))
        dumps_dir = Path(os.environ.get(ENV_DUMPS_DIR) or str(cwd / "dumps"))
        user_agent = os.environ.get(ENV_USER_AGENT) or DEFAULT_USER_AGENT
        http_timeout = _parse_float_env(ENV_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT_SECONDS)
        log_level = (os.environ.get(ENV_LOG_LEVEL) or "INFO").upper()
        api_key_raw = os.environ.get(ENV_HOSTED_API_KEY)
        api_key = api_key_raw.strip() if isinstance(api_key_raw, str) else None
        if api_key == "":
            api_key = None
        api_base = os.environ.get(ENV_HOSTED_API_BASE) or HOSTED_API_BASE_DEFAULT
        return cls(
            db_path=db_path,
            dumps_dir=dumps_dir,
            user_agent=user_agent,
            http_timeout_seconds=http_timeout,
            log_level=log_level,
            hosted_api_key=api_key,
            hosted_api_base=api_base,
        )


def _parse_float_env(var_name: str, default: float) -> float:
    raw = os.environ.get(var_name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            (
                f"Переменная окружения {var_name}='{raw}' — невалидное "
                f"число с плавающей точкой."
            ),
            hint=f"Ожидается положительное число секунд, например '{default}'.",
            details={"env_var": var_name, "value": raw},
        ) from exc
    if value <= 0:
        raise ValidationError(
            f"Переменная окружения {var_name}={raw} должна быть > 0.",
            hint=f"Значение по умолчанию: {default} секунд.",
            details={"env_var": var_name, "value": raw},
        )
    return value
