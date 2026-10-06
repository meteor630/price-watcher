"""Клиент протокола worker (DEVELOPMENT_ARCHITECTURE.md, раздел 8): claim, heartbeat, image, result.

Сервер — доверенный адрес из настроек (внутренняя сеть или свой домен), поэтому здесь обычный
HTTP-клиент, а не SafeFetcher магазинов. Сетевой сбой при отправке результата повторяется: сервер
принимает повтор без дубля. Токен worker не попадает в журнал.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2

logger = logging.getLogger(__name__)

TIMEOUT = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
RESULT_ATTEMPTS = 5
RETRY_PAUSE_S = 3.0


class LeaseLost(Exception):
    """Сервер не знает задание этого worker: аренда истекла или задание чужое."""


class WorkerRejected(Exception):
    """Сервер не принял токен worker: токен неверен, отозван или worker отключён — продолжать нельзя."""


class ServerUnavailable(Exception):
    """Сервер не ответил или ответил ошибкой — повторить позже."""


class ImageRefused(Exception):
    """Сервер не принял фото (422): файл не прошёл его проверку."""


@dataclass(frozen=True, slots=True)
class Task:
    task_id: int
    url: str
    region_code: str
    store_host: str
    parser_key: str
    fetch_method: str | None
    min_delay_ms: int
    need_image: bool


class ApiClient:
    def __init__(self, client: httpx2.Client, token: str, sleep: Callable[[float], None] = time.sleep) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self._sleep = sleep

    def claim(self) -> Task | None:
        data = self._post("/worker/v1/claim", None)
        task = data.get("task") if isinstance(data, dict) else None
        if task is None:
            return None
        return Task(
            int(task["task_id"]), str(task["url"]), str(task["region_code"]), str(task["store_host"]),
            str(task["parser_key"]), task.get("fetch_method"), int(task["min_delay_ms"]), bool(task["need_image"]),
        )

    def heartbeat(self, task_id: int) -> None:
        self._post("/worker/v1/heartbeat", {"task_id": task_id})

    def image(self, task_id: int, webp: bytes) -> None:
        self._send("/worker/v1/image", params={"task_id": task_id}, content=webp, content_type="image/webp")

    def result(self, task_id: int, payload: dict[str, Any]) -> None:
        for attempt in range(1, RESULT_ATTEMPTS + 1):
            try:
                self._post("/worker/v1/result", {"task_id": task_id, **payload})
                return
            except ServerUnavailable:
                if attempt == RESULT_ATTEMPTS:
                    raise
                logger.warning("Сервер не принял результат, повтор", extra={"task_id": task_id, "attempt": attempt})
                self._sleep(RETRY_PAUSE_S * attempt)

    def _post(self, path: str, body: dict[str, Any] | None) -> Any:
        return self._send(path, json=body)

    def _send(
        self, path: str, *, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None,
        content: bytes | None = None, content_type: str | None = None,
    ) -> Any:
        headers = self._headers if content_type is None else {**self._headers, "Content-Type": content_type}
        try:
            response = self._client.post(path, json=json, params=params, content=content, headers=headers)
        except httpx2.TransportError as error:
            raise ServerUnavailable(type(error).__name__) from error
        if response.status_code == 401:
            raise WorkerRejected(path)
        if response.status_code == 404:
            raise LeaseLost(path)
        if response.status_code == 422:
            raise ImageRefused(path)
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise ServerUnavailable(f"{path}: HTTP {response.status_code}")
        return response.json()


def http_client(api_url: str) -> httpx2.Client:
    # Прокси из окружения не используются: worker ходит к своему серверу напрямую.
    return httpx2.Client(base_url=api_url, timeout=TIMEOUT, trust_env=False)
