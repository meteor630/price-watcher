"""Клиент протокола worker (DEVELOPMENT_ARCHITECTURE.md, раздел 8): claim, heartbeat, image, result.

Сервер — адрес из настроек (свой домен), поэтому здесь обычный HTTP-клиент, а не SafeFetcher магазинов.
Сетевой сбой при отправке результата повторяется: сервер принимает повтор без дубля. Токен worker не попадает в журнал.

Ответы сервера — недоверенный ввод (сервер может быть захвачен): тело читается потоком не больше 64 КБ, задание
разбирается строго — ровно известные поля, правильные типы и пределы; лишнее поле, чужой тип или слишком большой
ответ — ServerAnswerRejected, и worker останавливается, ничего не открыв.
"""

import json as json_module
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

TIMEOUT = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
RESULT_ATTEMPTS = 5
RETRY_PAUSE_S = 3.0
ANSWER_LIMIT = 64 * 1024


class LeaseLost(Exception):
    """Сервер не знает задание этого worker: аренда истекла или задание чужое."""


class WorkerRejected(Exception):
    """Сервер не принял токен worker: токен неверен, отозван или worker отключён — продолжать нельзя."""


class ServerAnswerRejected(Exception):
    """Ответ сервера не по схеме или больше лимита: продолжать нельзя — сервер, возможно, захвачен."""


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


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: Annotated[int, Field(ge=1, le=2**62)]
    url: Annotated[str, Field(min_length=1, max_length=2048)]
    region_code: Annotated[str, Field(min_length=1, max_length=20)]
    store_host: Annotated[str, Field(min_length=1, max_length=253)]
    parser_key: Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")]
    fetch_method: Literal["http", "browser"] | None
    min_delay_ms: Annotated[int, Field(ge=0, le=600_000)]
    lease_until: Annotated[str, Field(max_length=40)]
    need_image: bool


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: TaskIn | None


class ApiClient:
    def __init__(self, client: httpx2.Client, token: str, sleep: Callable[[float], None] = time.sleep) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self._sleep = sleep

    def claim(self) -> Task | None:
        try:
            task = ClaimIn.model_validate(self._post("/worker/v1/claim", None)).task
        except ValidationError as error:
            raise ServerAnswerRejected("claim: " + ", ".join(".".join(map(str, item["loc"])) or "тело" for item in error.errors())) from None
        if task is None:
            return None
        return Task(task.task_id, task.url, task.region_code, task.store_host, task.parser_key, task.fetch_method, task.min_delay_ms, task.need_image)

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
            with self._client.stream("POST", path, json=json, params=params, content=content, headers=headers) as response:
                status = response.status_code
                body = _read_limited(response, path) if status == 200 else b""
        except httpx2.TransportError as error:
            raise ServerUnavailable(type(error).__name__) from error
        if status == 401:
            raise WorkerRejected(path)
        if status == 404:
            raise LeaseLost(path)
        if status == 422:
            raise ImageRefused(path)
        if status == 204:
            return None
        if status != 200:
            raise ServerUnavailable(f"{path}: HTTP {status}")
        try:
            return json_module.loads(body)
        except ValueError:
            raise ServerAnswerRejected(f"{path}: не JSON") from None


def _read_limited(response: httpx2.Response, path: str) -> bytes:
    body = b""
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > ANSWER_LIMIT:
            raise ServerAnswerRejected(f"{path}: ответ больше {ANSWER_LIMIT} байт")
    return body


def http_client(api_url: str) -> httpx2.Client:
    # Прокси из окружения не используются: worker ходит к своему серверу напрямую.
    return httpx2.Client(base_url=api_url, timeout=TIMEOUT, trust_env=False, follow_redirects=False)
