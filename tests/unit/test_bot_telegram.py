"""Клиент Telegram на подменённом транспорте: адрес и тело запросов, разбор обновлений, ответы Telegram об ошибках."""

import json

import httpx2
import pytest

from pricewatch.bot.telegram import (
    MESSAGE_MAX,
    ChatUnreachable,
    TelegramClient,
    TelegramConflict,
    TelegramRejected,
    TelegramUnavailable,
    Update,
)

TOKEN = "123456789:" + "A" * 35


def client(handler) -> tuple[TelegramClient, list[httpx2.Request]]:  # type: ignore[no-untyped-def]
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    http = httpx2.Client(base_url="https://api.telegram.org", transport=httpx2.MockTransport(record))
    return TelegramClient(http, TOKEN), seen


def ok(result: object) -> httpx2.Response:
    return httpx2.Response(200, json={"ok": True, "result": result})


def message(update_id: int, chat: dict, text: str | None = "/code") -> dict:
    body: dict = {"message_id": 1, "chat": chat}
    if text is not None:
        body["text"] = text
    return {"update_id": update_id, "message": body}


def test_updates_keep_only_private_text_messages() -> None:
    telegram, seen = client(lambda request: ok([
        message(10, {"id": 77, "type": "private"}, "/start abc"),
        message(11, {"id": -100, "type": "group"}, "/code"),
        message(12, {"id": 77, "type": "private"}, None),
        {"update_id": 13, "edited_message": {"chat": {"id": 77, "type": "private"}, "text": "x"}},
    ]))

    updates = telegram.updates(9, 10)

    assert updates == [Update(10, 77, "/start abc"), Update(11, None, None), Update(12, None, None), Update(13, None, None)]
    assert seen[0].url.path == f"/bot{TOKEN}/getUpdates"
    assert json.loads(seen[0].content) == {"timeout": 10, "allowed_updates": ["message"], "offset": 9}


def test_first_poll_has_no_offset() -> None:
    telegram, seen = client(lambda request: ok([]))

    telegram.updates(None, 10)

    assert "offset" not in json.loads(seen[0].content)


def test_send_is_plain_text_and_cut_to_telegram_limit() -> None:
    telegram, seen = client(lambda request: ok({"message_id": 5}))

    telegram.send(77, "<b>Лампа</b> " + "x" * MESSAGE_MAX)

    body = json.loads(seen[0].content)
    assert seen[0].url.path == f"/bot{TOKEN}/sendMessage"
    assert body["chat_id"] == 77 and len(body["text"]) == MESSAGE_MAX and body["text"].startswith("<b>Лампа</b>")
    assert "parse_mode" not in body and body["link_preview_options"] == {"is_disabled": True}


def test_set_commands() -> None:
    telegram, seen = client(lambda request: ok(True))

    telegram.set_commands([("code", "Код для приложения")])

    assert json.loads(seen[0].content) == {"commands": [{"command": "code", "description": "Код для приложения"}]}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}, TelegramRejected),
        (404, {"ok": False, "error_code": 404, "description": "Not Found"}, TelegramRejected),
        (409, {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates request"}, TelegramConflict),
        (403, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}, ChatUnreachable),
        (400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, ChatUnreachable),
        (502, None, TelegramUnavailable),
        (200, {"ok": False}, TelegramUnavailable),
    ],
)
def test_telegram_errors(status: int, body: dict | None, error: type[Exception]) -> None:
    telegram, _ = client(lambda request: httpx2.Response(status, json=body) if body is not None else httpx2.Response(status, text="<html>Bad Gateway</html>"))

    with pytest.raises(error) as caught:
        telegram.send(77, "текст")

    assert TOKEN not in str(caught.value)


def test_too_many_requests_carries_retry_after() -> None:
    telegram, _ = client(lambda request: httpx2.Response(429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}))

    with pytest.raises(TelegramUnavailable) as caught:
        telegram.send(77, "текст")

    assert caught.value.retry_after_s == 7


def test_network_failure_is_unavailable_without_token() -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}")

    telegram, _ = client(broken)

    with pytest.raises(TelegramUnavailable) as caught:
        telegram.updates(None, 10)

    assert TOKEN not in str(caught.value) and caught.value.retry_after_s is None
