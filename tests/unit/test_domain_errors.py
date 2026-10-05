"""pricewatch.domain.errors: каталог кодов совпадает с документами, сообщения безопасны."""

import re
from pathlib import Path

import pytest
import yaml

from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = (ROOT / "DEVELOPMENT_ARCHITECTURE.md").read_text(encoding="utf-8")
OPENAPI = yaml.safe_load((ROOT / "openapi" / "v1.yaml").read_text(encoding="utf-8"))


def architecture_codes() -> set[str]:
    section = ARCHITECTURE.split("## 10.", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if "Коды" in line and ":" in line]
    return {code for line in lines for code in re.findall(r"`([A-Z_]+)`", line)}


def openapi_codes() -> set[str]:
    error = OPENAPI["components"]["schemas"]["ErrorResponse"]["properties"]["error"]
    return set(error["properties"]["code"]["x-known-values"])


def test_codes_match_architecture_section_10() -> None:
    assert {code.value for code in ErrorCode} == architecture_codes()


def test_openapi_codes_are_known() -> None:
    assert openapi_codes() <= {code.value for code in ErrorCode}


def test_every_code_has_message() -> None:
    assert set(SAFE_MESSAGES) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_messages_are_plain_russian_sentences(code: ErrorCode) -> None:
    message = SAFE_MESSAGES[code]

    assert re.search(r"[А-Яа-яЁё]", message)
    assert message.endswith(".")
    assert len(message) <= 120
    assert not re.search(r"[A-Z_]{4,}|Traceback|Exception|https?://|\{|\}", message)


def test_error_carries_code_and_catalog_message() -> None:
    error = PriceWatcherError(ErrorCode.STORE_NOT_ALLOWED)

    assert error.code is ErrorCode.STORE_NOT_ALLOWED
    assert error.safe_message == "Этот магазин пока не подключён. Попросите администратора добавить его."
    assert str(error) == f"STORE_NOT_ALLOWED: {error.safe_message}"
    assert dict(error.details) == {}


def test_details_are_copied_and_read_only() -> None:
    fields = {"fields": {"quantity": "от 1 до 999"}}
    error = PriceWatcherError(ErrorCode.VALIDATION_ERROR, fields)
    fields["fields"] = {}

    assert error.details["fields"] == {"quantity": "от 1 до 999"}
    with pytest.raises(TypeError):
        error.details["extra"] = 1  # type: ignore[index]


def test_technical_cause_stays_in_chain_not_in_message() -> None:
    try:
        try:
            raise ConnectionError("postgresql://user:secret@db/pricewatch refused")
        except ConnectionError as cause:
            raise PriceWatcherError(ErrorCode.DB_ERROR) from cause
    except PriceWatcherError as error:
        assert "secret" not in str(error)
        assert "secret" in str(error.__cause__)


@pytest.mark.parametrize("bad_code", ["INVALID_URL", "UNKNOWN", None, 42])
def test_code_must_be_from_catalog(bad_code: object) -> None:
    with pytest.raises(TypeError):
        PriceWatcherError(bad_code)  # type: ignore[arg-type]


def test_unknown_code_cannot_be_created() -> None:
    with pytest.raises(ValueError):
        ErrorCode("UNKNOWN")
