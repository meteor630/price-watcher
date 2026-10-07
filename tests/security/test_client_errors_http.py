"""POST /api/v1/client_errors: 201 с error_id, повтор ключа — тот же ответ, строгое тело, лимит 429, секреты не хранятся, без токена — 401."""

import re
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_dashboard_http import NOW, bearer, client, engine, run_sql, schema, user_with_list  # noqa: F401 — фикстуры модуля

ERROR_SCHEMA = schema("ErrorResponse")


def body(**changes: object) -> dict[str, object]:
    return {"app_version": "1.4.0", "os_version": "iOS 26.0", "screen": "Товар", "error_code": "decoding",
            "message": "Нет поля price_minor", "occurred_at": (NOW - timedelta(minutes=1)).isoformat()} | changes


def post(client: TestClient, headers: dict[str, str], payload: object, key: str | None = None):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/client_errors", json=payload, headers=headers | {"Idempotency-Key": key or uuid.uuid4().hex})


def test_report_is_accepted_once_and_cleaned(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, user_with_list(engine, items=0))
    key = uuid.uuid4().hex

    first = post(client, headers, body(message="https://x.example/a?session=abc Bearer zzz"), key)
    again = post(client, headers, body(message="https://x.example/a?session=abc Bearer zzz"), key)

    assert first.status_code == 201 and re.fullmatch(r"E-[A-Z2-9]{10}", first.json()["error_id"])
    assert again.status_code == 201 and again.json() == first.json() and again.headers["Idempotency-Replayed"] == "true"
    stored = run_sql(engine, "SELECT message FROM client_error_reports WHERE error_id = :e", e=first.json()["error_id"])
    assert stored == "https://x.example/a Bearer [скрыто]"
    assert run_sql(engine, "SELECT count(*) FROM client_error_reports WHERE error_id = :e", e=first.json()["error_id"]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        body(extra="x"),
        body(message="я" * 2001),
        body(screen="э" * 81),
        body(occurred_at="вчера"),
        body(occurred_at="2026-10-06T12:00:00"),
        body(occurred_at=(NOW + timedelta(hours=1)).isoformat()),
        {key: value for key, value in body().items() if key != "error_code"},
        body(app_version=14),
    ],
)
def test_strict_body(client: TestClient, engine: Engine, payload: object) -> None:
    response = post(client, bearer(client, engine, user_with_list(engine, items=0)), payload)

    assert response.status_code == 422
    ERROR_SCHEMA.validate(response.json())


def test_limit_per_device(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=0)
    headers = bearer(client, engine, user)
    device = run_sql(engine, "SELECT max(id) FROM devices WHERE user_id = :u", u=user)
    for index in range(20):
        run_sql(engine, "INSERT INTO client_error_reports (error_id, user_id, device_id, app_version, os_version, screen, error_code, message, occurred_at, received_at) "
                        "VALUES (:e, :u, :d, '1', '1', 's', 'c', 'm', :t, :t) RETURNING id",
                e=f"E-L{uuid.uuid4().hex[:9]}", u=user, d=device, t=NOW - timedelta(minutes=30) + timedelta(seconds=index))

    refused = post(client, headers, body())

    assert refused.status_code == 429 and refused.json()["error"]["code"] == "RATE_LIMITED"


def test_needs_token_and_key(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, user_with_list(engine, items=0))

    assert client.post("/api/v1/client_errors", json=body(), headers={"Idempotency-Key": uuid.uuid4().hex}).status_code == 401
    assert client.post("/api/v1/client_errors", json=body(), headers=headers).status_code == 422
