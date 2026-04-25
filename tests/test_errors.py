"""Тесты иерархии исключений `McpEgrulError` и сериализации в dict.

Покрывает:
    * `to_dict()` — все комбинации (с hint/без, с details/без);
    * все подклассы — наследуют `code` из `constants`;
    * `HostedAuthError` / `ProRequiredError` — для hosted Pro контракта.
"""

from __future__ import annotations

from mcp_egrul.constants import (
    ERROR_CODE_AUTH_REQUIRED,
    ERROR_CODE_BULK_TOO_LARGE,
    ERROR_CODE_NOT_FOUND,
    ERROR_CODE_PRO_REQUIRED,
    ERROR_CODE_RATE_LIMIT,
    ERROR_CODE_SOURCE_UNAVAILABLE,
    ERROR_CODE_VALIDATION,
)
from mcp_egrul.errors import (
    BulkTooLargeError,
    HostedAuthError,
    McpEgrulError,
    NotFoundError,
    NothingToImportError,
    ProRequiredError,
    RateLimitedError,
    SourceUnavailableError,
    ValidationError,
)


class TestToDict:
    """`McpEgrulError.to_dict()` — канонический payload для MCP-клиента."""

    def test_to_dict_minimal_no_hint_no_details(self) -> None:
        """Без hint и без details — payload содержит только error/code/message."""
        err = ValidationError("m")
        payload = err.to_dict()
        assert payload == {
            "error": True,
            "code": ERROR_CODE_VALIDATION,
            "message": "m",
        }
        assert "hint" not in payload
        assert "details" not in payload

    def test_to_dict_with_hint_no_details(self) -> None:
        """С hint, но без details — details в payload отсутствует."""
        err = ValidationError("m", hint="передайте ИНН")
        payload = err.to_dict()
        assert payload["hint"] == "передайте ИНН"
        assert "details" not in payload

    def test_to_dict_with_details_no_hint(self) -> None:
        err = ValidationError("m", details={"x": 1})
        payload = err.to_dict()
        assert payload["details"] == {"x": 1}
        assert "hint" not in payload

    def test_to_dict_with_both(self) -> None:
        err = ValidationError("m", hint="h", details={"x": 1})
        payload = err.to_dict()
        assert payload["hint"] == "h"
        assert payload["details"] == {"x": 1}

    def test_empty_details_dict_not_included(self) -> None:
        """details=None → self.details={}, `if self.details:` False → не добавляется.

        Это ветка 71->73 в errors.py: пустой dict фальшивый, пропускаем key.
        """
        err = ValidationError("m", details=None)
        assert err.details == {}
        payload = err.to_dict()
        assert "details" not in payload


class TestSubclassCodes:
    """Каждый подкласс должен иметь стабильный `code` из constants.py."""

    def test_validation(self) -> None:
        assert ValidationError.code == ERROR_CODE_VALIDATION

    def test_not_found(self) -> None:
        assert NotFoundError.code == ERROR_CODE_NOT_FOUND

    def test_source_unavailable(self) -> None:
        assert SourceUnavailableError.code == ERROR_CODE_SOURCE_UNAVAILABLE

    def test_rate_limited(self) -> None:
        assert RateLimitedError.code == ERROR_CODE_RATE_LIMIT

    def test_bulk_too_large(self) -> None:
        assert BulkTooLargeError.code == ERROR_CODE_BULK_TOO_LARGE

    def test_nothing_to_import(self) -> None:
        # Явный строковый литерал — для этого кода нет константы.
        assert NothingToImportError.code == "nothing_to_import"

    def test_hosted_auth(self) -> None:
        assert HostedAuthError.code == ERROR_CODE_AUTH_REQUIRED

    def test_pro_required(self) -> None:
        assert ProRequiredError.code == ERROR_CODE_PRO_REQUIRED


class TestBaseException:
    def test_base_is_exception(self) -> None:
        """`McpEgrulError` — подкласс Exception, можно raise/catch как обычное."""
        assert issubclass(McpEgrulError, Exception)

    def test_base_str_returns_message_ru(self) -> None:
        err = ValidationError("тестовое сообщение")
        assert str(err) == "тестовое сообщение"
