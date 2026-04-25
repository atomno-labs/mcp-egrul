"""Валидаторы ИНН и ОГРН по официальным алгоритмам ФНС.

ИНН (Идентификационный номер налогоплательщика):
    * 10 цифр — юридическое лицо.
    * 12 цифр — физическое лицо или ИП (Индивидуальный предприниматель).
    Контрольная цифра — взвешенная сумма по модулю 11 затем модулю 10.

ОГРН/ОГРНИП (Основной государственный регистрационный номер):
    * 13 цифр — юр.лицо.
    * 15 цифр — ИП.
    Контрольная цифра = (N mod 11) mod 10 для 13-значного
                        (N mod 13) mod 10 для 15-значного,
    где N — все предыдущие цифры как целое.

Источники:
    * Приказ МНС России от 03.03.2004 N БГ-3-09/178.
    * Постановление Правительства РФ от 19.06.2002 N 438 (ОГРН).
"""

from __future__ import annotations

from .constants import (
    INN_INDIVIDUAL_LENGTH,
    INN_LEGAL_LENGTH,
    OGRN_LEGAL_LENGTH,
    OGRNIP_LENGTH,
)
from .errors import ValidationError

_INN_10_WEIGHTS: tuple[int, ...] = (2, 4, 10, 3, 5, 9, 4, 6, 8, 0)
_INN_12_WEIGHTS_1: tuple[int, ...] = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0, 0)
_INN_12_WEIGHTS_2: tuple[int, ...] = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0)

_OGRN_MOD_DIVISOR: int = 11
_OGRNIP_MOD_DIVISOR: int = 13


def _checksum_inn(digits: tuple[int, ...], weights: tuple[int, ...]) -> int:
    return sum(d * w for d, w in zip(digits, weights, strict=False)) % 11 % 10


def is_valid_inn(value: object) -> bool:
    """Проверить валидность ИНН (длина + контрольная цифра).

    Возвращает True только если на входе строка из цифр нужной длины
    и контрольная сумма сходится. Для любых других типов / форматов —
    False (без исключений; для строгого варианта используйте
    `assert_valid_inn`).
    """
    if not isinstance(value, str) or not value.isdigit():
        return False
    length = len(value)
    if length not in (INN_LEGAL_LENGTH, INN_INDIVIDUAL_LENGTH):
        return False

    digits = tuple(int(c) for c in value)

    if length == INN_LEGAL_LENGTH:
        return _checksum_inn(digits[:INN_LEGAL_LENGTH], _INN_10_WEIGHTS) == digits[9]

    check1 = _checksum_inn(digits[:11] + (0,), _INN_12_WEIGHTS_1)
    check2 = _checksum_inn(digits[:INN_INDIVIDUAL_LENGTH], _INN_12_WEIGHTS_2)
    return check1 == digits[10] and check2 == digits[11]


def is_valid_ogrn(value: object) -> bool:
    """Проверить валидность ОГРН (13 цифр) или ОГРНИП (15 цифр)."""
    if not isinstance(value, str) or not value.isdigit():
        return False
    length = len(value)
    if length not in (OGRN_LEGAL_LENGTH, OGRNIP_LENGTH):
        return False

    body = value[:-1]
    expected = int(value[-1])
    divisor = _OGRN_MOD_DIVISOR if length == OGRN_LEGAL_LENGTH else _OGRNIP_MOD_DIVISOR
    return int(body) % divisor % 10 == expected


def assert_valid_inn(value: str) -> str:
    """Бросает `ValidationError`, если ИНН невалиден; иначе возвращает значение.

    Сообщение и подсказка — русскоязычные, для AI-агента. Если клиент передал
    13 символов, скорее всего он перепутал с ОГРН — подсказка отражает это.
    """
    if is_valid_inn(value):
        return value

    hint: str | None = None
    if isinstance(value, str) and value.isdigit():
        length = len(value)
        if length == OGRN_LEGAL_LENGTH:
            hint = (
                "Похоже, что передан ОГРН (13 цифр). "
                "Для ОГРН используйте `search_by_ogrn`."
            )
        elif length == OGRNIP_LENGTH:
            hint = (
                "Похоже, что передан ОГРНИП (15 цифр). "
                "Для ОГРНИП используйте `search_by_ogrn`."
            )

    raise ValidationError(
        (
            f"Невалидный ИНН: '{value}'. "
            f"Ожидается {INN_LEGAL_LENGTH} цифр (юр.лицо) "
            f"или {INN_INDIVIDUAL_LENGTH} (ИП/физлицо) "
            f"с корректной контрольной цифрой."
        ),
        hint=hint,
        details={
            "input": value,
            "expected_length": [INN_LEGAL_LENGTH, INN_INDIVIDUAL_LENGTH],
        },
    )


def assert_valid_ogrn(value: str) -> str:
    """Бросает `ValidationError`, если ОГРН/ОГРНИП невалиден."""
    if is_valid_ogrn(value):
        return value

    hint: str | None = None
    if isinstance(value, str) and value.isdigit():
        length = len(value)
        if length == INN_LEGAL_LENGTH:
            hint = (
                "Похоже, что передан ИНН юр.лица (10 цифр). "
                "Для ИНН используйте `search_by_inn`."
            )
        elif length == INN_INDIVIDUAL_LENGTH:
            hint = (
                "Похоже, что передан ИНН ИП/физлица (12 цифр). "
                "Для ИНН используйте `search_by_inn`."
            )

    raise ValidationError(
        (
            f"Невалидный ОГРН: '{value}'. "
            f"Ожидается {OGRN_LEGAL_LENGTH} цифр (юр.лицо) "
            f"или {OGRNIP_LENGTH} (ИП) с корректной контрольной цифрой."
        ),
        hint=hint,
        details={
            "input": value,
            "expected_length": [OGRN_LEGAL_LENGTH, OGRNIP_LENGTH],
        },
    )


def detect_subject_type(inn: str) -> str:
    """По длине валидного ИНН определить тип субъекта.

    Возвращает 'legal_entity' для 10-значного ИНН, 'individual' — для
    12-значного. Для невалидного ИНН — бросает ValidationError.
    """
    assert_valid_inn(inn)
    return "legal_entity" if len(inn) == INN_LEGAL_LENGTH else "individual"


def detect_ogrn_subject_type(ogrn: str) -> str:
    """По длине валидного ОГРН определить тип субъекта."""
    assert_valid_ogrn(ogrn)
    return "legal_entity" if len(ogrn) == OGRN_LEGAL_LENGTH else "individual"
