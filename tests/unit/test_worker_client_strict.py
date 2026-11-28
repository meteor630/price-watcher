"""Клиент worker не доверяет серверу: ответ claim разбирается строго (ровно известные поля, типы, пределы), тело —
не больше 64 КБ, перенаправления не выполняются. Иначе — ServerAnswerRejected, задание не берётся."""

import json

import httpx2
import pytest

from pricewatch.worker.client import ANSWER_LIMIT, ApiClient, ServerAnswerRejected, ServerUnavailable, Task

TASK = {
    "task_id": 7, "url": "https://www.ozon.ru/product/1/", "region_code": "spb", "store_host": "ozon.ru", "parser_key": "ozon",
    "fetch_method": None, "min_delay_ms": 5000, "lease_until": "2026-11-28T12:02:00Z", "need_image": False,
}


def client_answering(body: bytes, status: int = 200, headers: dict[str, str] | None = None) -> ApiClient:
    def reply(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, content=body, headers=headers)
    return ApiClient(httpx2.Client(base_url="https://prices.example.ru", transport=httpx2.MockTransport(reply), follow_redirects=False), "w" * 43)


# --- обычный путь ---


def test_valid_claim_becomes_task() -> None:
    task = client_answering(json.dumps({"task": TASK}).encode()).claim()
    assert task == Task(7, "https://www.ozon.ru/product/1/", "spb", "ozon.ru", "ozon", None, 5000, False)


def test_empty_queue_is_none() -> None:
    assert client_answering(b'{"task": null}').claim() is None


# --- граница: ответ ровно на пределе ---


def test_answer_exactly_at_limit_is_read() -> None:
    body = json.dumps({"task": None}).encode()
    body = body[:-1] + b" " * (ANSWER_LIMIT - len(body)) + b"}"
    assert len(body) == ANSWER_LIMIT
    assert client_answering(body).claim() is None


# --- отказ ---


@pytest.mark.parametrize(
    "answer",
    [
        {"task": {**TASK, "proxy": "http://10.0.0.1:3128"}},
        {"task": TASK, "run": 1},
        {"task": {**TASK, "task_id": "7"}},
        {"task": {**TASK, "min_delay_ms": -1}},
        {"task": {**TASK, "need_image": "yes"}},
        {"task": {**TASK, "fetch_method": "shell"}},
        {"task": {**TASK, "parser_key": "../evil"}},
        {"task": {key: value for key, value in TASK.items() if key != "url"}},
        {"tasks": []},
        [],
    ],
    ids=["extra-task-field", "extra-top-field", "id-as-string", "negative-delay", "bool-as-string", "unknown-method",
         "bad-parser-key", "missing-url", "wrong-top-key", "not-object"],
)
def test_claim_not_matching_schema_is_rejected(answer: object) -> None:
    with pytest.raises(ServerAnswerRejected):
        client_answering(json.dumps(answer).encode()).claim()


def test_answer_bigger_than_limit_is_rejected() -> None:
    with pytest.raises(ServerAnswerRejected):
        client_answering(b'{"task": null}' + b" " * ANSWER_LIMIT).claim()


def test_not_json_answer_is_rejected() -> None:
    with pytest.raises(ServerAnswerRejected):
        client_answering(b"<html>captive portal</html>").claim()


def test_redirect_is_not_followed() -> None:
    client = client_answering(b"", status=307, headers={"location": "http://192.168.1.1/"})
    with pytest.raises(ServerUnavailable, match="HTTP 307"):
        client.claim()
