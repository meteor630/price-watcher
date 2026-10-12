"""Клиент внутреннего API сервера (/internal/v1) с токеном сервиса.

Сервер — доверенный адрес из настроек, поэтому обычный HTTP-клиент без прокси окружения. 401 — токен
не принят, бот останавливается; 404 у привязки и кода — понятный ответ пользователю; остальное — сервер
недоступен, повторить позже. Токен сервиса не попадает в журнал.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx2

TIMEOUT = httpx2.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


class ApiUnavailable(Exception):
    """Сервер не ответил или ответил ошибкой — повторить позже."""


class ServiceRejected(Exception):
    """Сервер не принял токен сервиса: токен неверен, отозван или сервис отключён — продолжать нельзя."""


class NotFound(Exception):
    """Сервер ответил 404: код привязки не подошёл, чат не привязан, сообщение уже отмечено."""


@dataclass(frozen=True, slots=True)
class PairingCode:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Outgoing:
    notification_id: int
    chat_id: int
    text: str


class InternalApi:
    def __init__(self, client: httpx2.Client, service_token: str) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {service_token}"}

    def link(self, code: str, chat_id: int) -> str:
        """Привязать чат по коду с сайта; имя пользователя. Код не подошёл — NotFound."""
        return str(self._send("POST", "/internal/v1/telegram/link", {"code": code, "chat_id": chat_id})["display_name"])

    def pairing_code(self, chat_id: int) -> PairingCode:
        """Код для приложения; чат не привязан или пользователь заблокирован — NotFound."""
        data = self._send("POST", "/internal/v1/telegram/pairing-code", {"chat_id": chat_id})
        return PairingCode(str(data["code"]), datetime.fromisoformat(str(data["expires_at"])))

    def claim(self, limit: int) -> list[Outgoing]:
        data = self._send("POST", "/internal/v1/outbox/claim", {"limit": limit})
        return [Outgoing(int(item["id"]), int(item["chat_id"]), str(item["text"])) for item in data["items"]]

    def sent(self, notification_id: int) -> None:
        self._send("POST", f"/internal/v1/outbox/{notification_id}/sent", None)

    def failed(self, notification_id: int) -> None:
        self._send("POST", f"/internal/v1/outbox/{notification_id}/failed", None)

    def _send(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        try:
            response = self._client.request(method, path, json=body, headers=self._headers)
        except httpx2.TransportError as error:
            raise ApiUnavailable(f"{path}: {type(error).__name__}") from error
        if response.status_code == 401:
            raise ServiceRejected(path)
        if response.status_code == 404:
            raise NotFound(path)
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise ApiUnavailable(f"{path}: HTTP {response.status_code}")
        return response.json()


def http_client(api_url: str) -> httpx2.Client:
    return httpx2.Client(base_url=api_url, timeout=TIMEOUT, trust_env=False)
