"""Клиент Telegram Bot API поверх httpx2: getUpdates (длинный опрос), sendMessage, setMyCommands.

Свой маленький клиент вместо библиотеки — решение владельца (этап 8): боту нужны три метода.
Токен бота — часть адреса запроса; он зарегистрирован как секрет и вырезается из журнала.
Сообщения уходят простым текстом без разметки: имена и названия из базы не превращаются в ссылки и теги.
"""

from dataclasses import dataclass
from typing import Any

import httpx2

API_ORIGIN = "https://api.telegram.org"
MESSAGE_MAX = 4096
TIMEOUT = httpx2.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
# Ответ getUpdates ждёт не дольше времени опроса; запас на сеть.
POLL_READ_MARGIN_S = 10.0


class TelegramUnavailable(Exception):
    """Telegram не ответил, ответил ошибкой сервера или просит подождать — повторить позже."""

    def __init__(self, reason: str, retry_after_s: int | None = None) -> None:
        super().__init__(reason)
        self.retry_after_s = retry_after_s


class TelegramConflict(TelegramUnavailable):
    """Обновления уже получает другой экземпляр бота (HTTP 409)."""


class TelegramRejected(Exception):
    """Telegram не принял токен бота — продолжать нельзя."""


class ChatUnreachable(Exception):
    """Сообщение в этот чат не доставить: пользователь заблокировал бота, чат не найден, неверный текст."""


@dataclass(frozen=True, slots=True)
class Update:
    update_id: int
    # Сообщение личного чата с текстом; остальные обновления (группы, правки, фото) боту не нужны.
    chat_id: int | None
    text: str | None


class TelegramClient:
    def __init__(self, client: httpx2.Client, token: str) -> None:
        self._client = client
        self._prefix = f"/bot{token}/"

    def updates(self, offset: int | None, poll_s: int) -> list[Update]:
        payload: dict[str, Any] = {"timeout": poll_s, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, read_timeout_s=poll_s + POLL_READ_MARGIN_S)
        return [_update(item) for item in result]

    def send(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text[:MESSAGE_MAX], "link_preview_options": {"is_disabled": True}})

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self._call("setMyCommands", {"commands": [{"command": name, "description": description} for name, description in commands]})

    def _call(self, method: str, payload: dict[str, Any], read_timeout_s: float | None = None) -> Any:
        timeout = TIMEOUT if read_timeout_s is None else httpx2.Timeout(connect=5.0, read=read_timeout_s, write=10.0, pool=5.0)
        try:
            response = self._client.post(self._prefix + method, json=payload, timeout=timeout)
        except httpx2.TransportError as error:
            raise TelegramUnavailable(f"{method}: {type(error).__name__}") from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
            return body.get("result")
        parameters = body.get("parameters") if isinstance(body, dict) else None
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if response.status_code in (401, 404):
            raise TelegramRejected(f"{method}: HTTP {response.status_code}")
        if response.status_code == 409:
            raise TelegramConflict(f"{method}: HTTP 409")
        if response.status_code in (400, 403):
            raise ChatUnreachable(f"{method}: HTTP {response.status_code}")
        raise TelegramUnavailable(f"{method}: HTTP {response.status_code}", retry_after if isinstance(retry_after, int) else None)


def http_client() -> httpx2.Client:
    # Прокси из окружения не используются: бот ходит в Telegram напрямую.
    return httpx2.Client(base_url=API_ORIGIN, timeout=TIMEOUT, trust_env=False)


def _update(item: dict[str, Any]) -> Update:
    message = item.get("message")
    if not isinstance(message, dict):
        return Update(int(item["update_id"]), None, None)
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(text, str):
        return Update(int(item["update_id"]), None, None)
    return Update(int(item["update_id"]), int(chat["id"]), text)
