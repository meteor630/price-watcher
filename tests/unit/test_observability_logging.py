"""pricewatch.observability: JSON-журнал, идентификаторы, вырезание секретов."""

import asyncio
import io
import json
import logging
import re
import sys

import pytest

from pricewatch.observability.ids import current_request_id, new_error_id, request_context
from pricewatch.observability.json_log import JsonFormatter, configure_logging
from pricewatch.observability.redaction import MASK, redact_text, redact_value

SECRET = "tok_9f8e7d6c5b4a"


@pytest.fixture
def log() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("pricewatch.test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def entries(stream: io.StringIO) -> list[dict[str, object]]:
    lines = stream.getvalue().splitlines()
    return [json.loads(line) for line in lines]


def test_entry_is_single_json_line_with_base_fields(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    logger.info("Цена обновлена: %s", "12 990 ₽")

    (entry,) = entries(stream)
    assert stream.getvalue().count("\n") == 1
    assert entry["message"] == "Цена обновлена: 12 990 ₽"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "pricewatch.test"
    assert entry["request_id"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", str(entry["ts"]))


def test_multiline_message_stays_one_line(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    logger.warning("первая строка\nвторая строка")

    assert len(stream.getvalue().splitlines()) == 1
    assert entries(stream)[0]["message"] == "первая строка\nвторая строка"


def test_extra_fields_and_error_id_are_included(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    logger.error("Сбой проверки", extra={"error_id": "E-ABCDEFGHJK", "error_code": "TIMEOUT", "store_id": 7})

    entry = entries(stream)[0]
    assert entry["error_id"] == "E-ABCDEFGHJK"
    assert entry["error_code"] == "TIMEOUT"
    assert entry["store_id"] == 7


def test_base_fields_win_over_extra(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    logger.info("настоящее", extra={"level": "FAKE", "logger": "fake", "ts": "fake"})

    entry = entries(stream)[0]
    assert (entry["level"], entry["logger"]) == ("INFO", "pricewatch.test")
    assert entry["ts"] != "fake"


def test_request_id_comes_from_context(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    with request_context("req-12345678"):
        logger.info("внутри")
    logger.info("снаружи")

    assert [e["request_id"] for e in entries(stream)] == ["req-12345678", None]


def test_request_context_is_isolated_between_tasks() -> None:
    async def handle(request_id: str) -> str | None:
        with request_context(request_id):
            await asyncio.sleep(0)
            return current_request_id()

    async def main() -> list[str | None]:
        return list(await asyncio.gather(handle("req-aaaaaaaa"), handle("req-bbbbbbbb")))

    assert asyncio.run(main()) == ["req-aaaaaaaa", "req-bbbbbbbb"]
    assert current_request_id() is None


def test_sensitive_extra_fields_are_masked_recursively(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    logger.info(
        "запрос",
        extra={
            "headers": {"Authorization": f"Bearer {SECRET}", "Cookie": f"session={SECRET}", "Accept": "json"},
            "body": {"refresh_token": SECRET, "pairing_code": "ABCD2345", "quantity": 2},
            "db_password": SECRET,
        },
    )

    raw = stream.getvalue()
    entry = entries(stream)[0]
    assert SECRET not in raw
    assert "ABCD2345" not in raw
    assert entry["headers"] == {"Authorization": MASK, "Cookie": MASK, "Accept": "json"}
    assert entry["body"] == {"refresh_token": MASK, "pairing_code": MASK, "quantity": 2}
    assert entry["db_password"] == MASK


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Bearer {SECRET}",
        f"проверка с заголовком cookie: session={SECRET}; theme=dark",
        f"connect postgresql://pricewatch:{SECRET}@db:5432/pricewatch",
        f"GET /auth/callback?code={SECRET}&state=ok",
        f"https://shop.example/item?id=5&access_token={SECRET}",
        f"login failed password={SECRET}",
        f"api_key={SECRET} rejected",
        f"Basic {SECRET}",
    ],
)
def test_secrets_are_removed_from_text(text: str) -> None:
    redacted = redact_text(text)

    assert SECRET not in redacted
    assert MASK in redacted


@pytest.mark.parametrize(
    "text",
    [
        "error_code=VERSION_CONFLICT item_id=12",
        "https://shop.example/item?id=5&color=red",
        "Цена 12 990 ₽, наличие: есть",
    ],
)
def test_ordinary_text_is_untouched(text: str) -> None:
    assert redact_text(text) == text


def test_traceback_is_included_and_redacted(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    try:
        raise ConnectionError(f"postgresql://pricewatch:{SECRET}@db/pricewatch refused")
    except ConnectionError:
        logger.exception("База недоступна", extra={"error_id": "E-ABCDEFGHJK"})

    entry = entries(stream)[0]
    assert SECRET not in stream.getvalue()
    assert "Traceback" in str(entry["exc"])
    assert "ConnectionError" in str(entry["exc"])
    assert entry["level"] == "ERROR"


def test_non_serializable_extra_becomes_redacted_repr(log: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = log

    class Dsn:
        def __repr__(self) -> str:
            return f"Dsn('postgresql://u:{SECRET}@db/x')"

    logger.info("объект", extra={"target": Dsn(), "tags": {"a"}})

    entry = entries(stream)[0]
    assert SECRET not in stream.getvalue()
    assert str(entry["target"]).startswith("Dsn(")
    assert entry["tags"] == ["a"]


def test_broken_record_is_not_lost() -> None:
    # Запись форматируется напрямую: pytest подключает свои обработчики к логгерам
    # без propagate, и его собственный форматтер упал бы на этой записи раньше нашего.
    record = logging.LogRecord(
        "pricewatch.test", logging.INFO, __file__, 1, "два аргумента: %s %s", ("только один",), None
    )

    entry = json.loads(JsonFormatter().format(record))
    assert entry["level"] == "INFO"
    assert entry["logger"] == "pricewatch.test"
    assert entry["format_error"] == "TypeError"


def test_redact_value_keeps_plain_types() -> None:
    assert redact_value({"n": 1, "ok": True, "none": None, "items": (1, "x")}) == {
        "n": 1,
        "ok": True,
        "none": None,
        "items": [1, "x"],
    }


def test_error_id_format_and_uniqueness() -> None:
    ids = {new_error_id() for _ in range(1000)}

    assert len(ids) == 1000
    assert all(re.fullmatch(r"E-[A-HJ-KM-NP-Z2-9]{10}", value) for value in ids)


def test_configure_logging_writes_json_to_stdout_once(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("INFO")
        configure_logging("INFO")
        logging.getLogger("pricewatch.any").info("одна запись")
        logging.getLogger("pricewatch.any").debug("не пишется")

        assert len(root.handlers) == 1
        assert [entry["message"] for entry in entries(stream)] == ["одна запись"]
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
