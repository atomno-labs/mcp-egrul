"""Тесты валидаторов ИНН и ОГРН (контрольные цифры по алгоритмам ФНС)."""

from __future__ import annotations

import pytest

from mcp_egrul.errors import ValidationError
from mcp_egrul.validators import (
    assert_valid_inn,
    assert_valid_ogrn,
    detect_ogrn_subject_type,
    detect_subject_type,
    is_valid_inn,
    is_valid_ogrn,
)
from tests.conftest import (
    VALID_INN_IE,
    VALID_INN_LEGAL,
    VALID_OGRN_LEGAL,
    VALID_OGRNIP,
)


class TestINN:
    @pytest.mark.parametrize("inn", VALID_INN_LEGAL)
    def test_valid_legal(self, inn: str) -> None:
        assert is_valid_inn(inn) is True
        assert detect_subject_type(inn) == "legal_entity"

    @pytest.mark.parametrize("inn", VALID_INN_IE)
    def test_valid_individual(self, inn: str) -> None:
        assert is_valid_inn(inn) is True
        assert detect_subject_type(inn) == "individual"

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "abc",
            "123",
            "12345678901",       # 11 цифр — нет такой длины
            "1111111111",        # контрольная не сходится
            "7707083894",        # Сбербанк с битой контрольной
            "1234567890",
            None,
            12345,
        ],
    )
    def test_invalid(self, value: object) -> None:
        assert is_valid_inn(value) is False

    def test_assert_raises_with_hint_for_ogrn_like(self) -> None:
        """13-значный вход — подсказка «это похоже на ОГРН»."""
        with pytest.raises(ValidationError) as info:
            assert_valid_inn("1027700132195")
        err = info.value
        assert err.code == "invalid_input"
        assert err.hint is not None
        assert "ОГРН" in err.hint

    def test_assert_raises_with_hint_for_ogrnip_like(self) -> None:
        """15-значный вход в ИНН — подсказка «это похоже на ОГРНИП»."""
        with pytest.raises(ValidationError) as info:
            assert_valid_inn("304500116000061")
        err = info.value
        assert err.code == "invalid_input"
        assert err.hint is not None
        assert "ОГРНИП" in err.hint

    def test_assert_raises_without_hint_for_garbage(self) -> None:
        with pytest.raises(ValidationError) as info:
            assert_valid_inn("1234567890")
        assert info.value.code == "invalid_input"

    def test_assert_no_hint_when_value_is_not_digit(self) -> None:
        """Строка с буквами → hint=None (не 13/15 цифр → нечего подсказывать)."""
        with pytest.raises(ValidationError) as info:
            assert_valid_inn("abcdefghij")
        assert info.value.hint is None
        assert info.value.code == "invalid_input"

    def test_assert_no_hint_when_value_is_not_str(self) -> None:
        """Числовое значение → isinstance(value, str)==False → hint=None."""
        with pytest.raises(ValidationError) as info:
            assert_valid_inn(1234567890)  # type: ignore[arg-type]
        assert info.value.hint is None

    def test_assert_passes(self) -> None:
        assert assert_valid_inn("7707083893") == "7707083893"

    def test_detect_invalid_raises(self) -> None:
        with pytest.raises(ValidationError):
            detect_subject_type("1111111111")


class TestOGRN:
    @pytest.mark.parametrize("ogrn", VALID_OGRN_LEGAL)
    def test_valid_legal(self, ogrn: str) -> None:
        assert is_valid_ogrn(ogrn) is True
        assert detect_ogrn_subject_type(ogrn) == "legal_entity"

    @pytest.mark.parametrize("ogrn", VALID_OGRNIP)
    def test_valid_individual(self, ogrn: str) -> None:
        assert is_valid_ogrn(ogrn) is True
        assert detect_ogrn_subject_type(ogrn) == "individual"

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "abc",
            "1234567890",         # длина INN
            "1027700132196",      # ОГРН Сбера с битой контрольной
            "1234567890123",      # длина ок, контрольная не сходится
            None,
        ],
    )
    def test_invalid(self, value: object) -> None:
        assert is_valid_ogrn(value) is False

    def test_assert_raises_with_hint_for_inn_like(self) -> None:
        """10-значный вход — подсказка «это похоже на ИНН»."""
        with pytest.raises(ValidationError) as info:
            assert_valid_ogrn("7707083893")
        err = info.value
        assert err.hint is not None
        assert "ИНН" in err.hint

    def test_assert_raises_with_hint_for_inn_ie_like(self) -> None:
        """12-значный вход в ОГРН — подсказка «это похоже на ИНН ИП/физлица»."""
        with pytest.raises(ValidationError) as info:
            assert_valid_ogrn("500100732259")
        err = info.value
        assert err.hint is not None
        assert "ИНН ИП" in err.hint or "физлица" in err.hint

    def test_assert_passes_legal(self) -> None:
        assert assert_valid_ogrn("1027700132195") == "1027700132195"

    def test_assert_passes_ie(self) -> None:
        assert assert_valid_ogrn("304500116000061") == "304500116000061"

    def test_assert_no_hint_when_value_is_not_digit(self) -> None:
        """Строка с буквами в ОГРН → hint=None."""
        with pytest.raises(ValidationError) as info:
            assert_valid_ogrn("abcdefghijklm")
        assert info.value.hint is None

    def test_assert_no_hint_when_value_is_not_str(self) -> None:
        """Целое число → isinstance(value, str)==False → hint=None."""
        with pytest.raises(ValidationError) as info:
            assert_valid_ogrn(1027700132195)  # type: ignore[arg-type]
        assert info.value.hint is None
