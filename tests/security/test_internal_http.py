"""Внутренний API бота по HTTP: только токен сервиса (не worker, не приложение, не cookie сайта), пульс на каждый
запрос, строгие тела, привязка и код для приложения, сообщения на отправку; токен не попадает в журнал."""

import logging
import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.api.web_auth import SESSION_COOKIE
from pricewatch.application.app_auth import issue_pairing_code
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.services import create_service
from pricewatch.application.telegram import BATCH_MAX, issue_link_code
from pricewatch.application.worker_protocol import create_worker
from pricewatch.config.settings import Settings
from pricewatch.domain.accounts import Role, User
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "internal-http-key-" + "k" * 30
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    settings = Settings(env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), PasswordHasher(time_cost=1, memory_cost=8192), lambda: NOW, MediaStore(tmp_path), LinkCatalog())
    return TestClient(create_app(settings, context), base_url="https://testserver")


def service_token(engine: Engine) -> str:
    return create_service(SqlUnitOfWork(engine), KEY.encode(), f"bot-{uuid.uuid4().hex[:8]}", NOW - timedelta(days=1))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sql(engine: Engine, statement: str, **params: object) -> object:
    with engine.begin() as db:
        return db.execute(text(statement), params).scalar_one()


def new_chat() -> int:
    return random.randrange(10**9, 10**12)


def person(engine: Engine, chat: int | None = None) -> User:
    user_id = sql(
        engine, "INSERT INTO users (login, password_hash, role, display_name, telegram_chat_id) VALUES (:l, 'h', 'user', '<b>Аня</b>', :c) RETURNING id",
        l=f"i{uuid.uuid4().hex[:10]}", c=chat,
    )
    return User(user_id, "i", Role.USER, "<b>Аня</b>", False)  # type: ignore[arg-type]


def telegram_message(engine: Engine, user: User) -> int:
    return sql(  # type: ignore[return-value]
        engine,
        "INSERT INTO notifications (user_id, channel, kind, dedup_key, payload_json, created_at) "
        "VALUES (:u, 'telegram', 'unexpected_error', md5(random()::text), "
        "'{\"exception\": \"KeyError\", \"method\": \"GET\", \"route\": \"/web/\", \"error_id\": \"E-AAAAAAAAAA\"}', :t) RETURNING id",
        u=user.id, t=NOW - timedelta(minutes=1),
    )


ROUTES = [
    ("POST", "/internal/v1/telegram/link", {"code": "x", "chat_id": 1}),
    ("POST", "/internal/v1/telegram/pairing-code", {"chat_id": 1}),
    ("POST", "/internal/v1/outbox/claim", {"limit": 1}),
    ("POST", "/internal/v1/outbox/1/sent", None),
    ("POST", "/internal/v1/outbox/1/failed", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_internal_route_needs_service_token(client: TestClient, engine: Engine, method: str, path: str, body: dict | None) -> None:
    worker = create_worker(SqlUnitOfWork(engine), KEY.encode(), f"w-{uuid.uuid4().hex[:8]}", NOW)
    for headers in ({}, bearer("made-up"), bearer(worker), {"Authorization": "Basic eDp5"}):
        response = client.request(method, path, json=body, headers=headers)
        assert response.status_code == 401, (headers, response.text)
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_app_token_and_site_cookie_do_not_open_internal_routes(client: TestClient, engine: Engine) -> None:
    admin = sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'admin', 'A') RETURNING id", l=f"a{uuid.uuid4().hex[:10]}")
    code = issue_pairing_code(SqlUnitOfWork(engine), KEY.encode(), admin, NOW).code  # type: ignore[arg-type]
    tokens = client.post("/api/v1/auth/pair", json={"code": code, "device_name": "iPad"}).json()
    client.cookies.set(SESSION_COOKIE, "some-session")

    assert client.post("/internal/v1/outbox/claim", json={"limit": 1}, headers=bearer(tokens["access_token"])).status_code == 401
    assert client.post("/internal/v1/outbox/claim", json={"limit": 1}).status_code == 401


def test_service_token_does_not_open_app_or_worker_routes(client: TestClient, engine: Engine) -> None:
    token = service_token(engine)

    assert client.get("/api/v1/dashboard", headers=bearer(token)).status_code == 401
    assert client.post("/worker/v1/claim", headers=bearer(token)).status_code == 401


def test_request_marks_bot_alive(client: TestClient, engine: Engine) -> None:
    token = service_token(engine)

    response = client.post("/internal/v1/outbox/claim", json={"limit": 1}, headers=bearer(token))

    assert response.status_code == 200
    assert sql(engine, "SELECT count(*) FROM services WHERE last_seen_at = :t", t=NOW) >= 1  # type: ignore[operator]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/internal/v1/telegram/link", {"code": "x", "chat_id": "1"}),
        ("/internal/v1/telegram/link", {"code": "", "chat_id": 1}),
        ("/internal/v1/telegram/link", {"code": "x" * 65, "chat_id": 1}),
        ("/internal/v1/telegram/link", {"code": "x", "chat_id": 1, "user_id": 1}),
        ("/internal/v1/telegram/link", {"code": "x", "chat_id": 2**63}),
        ("/internal/v1/telegram/pairing-code", {"chat_id": 1.5}),
        ("/internal/v1/telegram/pairing-code", {}),
        ("/internal/v1/outbox/claim", {"limit": 0}),
        ("/internal/v1/outbox/claim", {"limit": BATCH_MAX + 1}),
        ("/internal/v1/outbox/claim", {"limit": True}),
        ("/internal/v1/outbox/0/sent", None),
        ("/internal/v1/outbox/abc/failed", None),
    ],
)
def test_strict_bodies(client: TestClient, engine: Engine, path: str, body: dict | None) -> None:
    response = client.post(path, json=body, headers=bearer(service_token(engine)))

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_link_and_pairing_code_over_http(client: TestClient, engine: Engine) -> None:
    token, user, chat = service_token(engine), person(engine), new_chat()
    code = issue_link_code(SqlUnitOfWork(engine), KEY.encode(), user, NOW).code

    before = client.post("/internal/v1/telegram/pairing-code", json={"chat_id": chat}, headers=bearer(token))
    linked = client.post("/internal/v1/telegram/link", json={"code": code, "chat_id": chat}, headers=bearer(token))
    again = client.post("/internal/v1/telegram/link", json={"code": code, "chat_id": chat}, headers=bearer(token))
    issued = client.post("/internal/v1/telegram/pairing-code", json={"chat_id": chat}, headers=bearer(token))
    paired = client.post("/api/v1/auth/pair", json={"code": issued.json()["code"], "device_name": "iPhone"})

    assert before.status_code == 404
    assert linked.status_code == 200 and linked.json() == {"display_name": "<b>Аня</b>"}
    assert again.status_code == 404
    assert issued.status_code == 200 and issued.json()["expires_at"].endswith("+00:00")
    assert paired.status_code == 200
    assert sql(engine, "SELECT count(*) FROM devices WHERE user_id = :u", u=user.id) == 1


def test_outbox_claim_sent_failed_over_http(client: TestClient, engine: Engine) -> None:
    token, chat = service_token(engine), new_chat()
    user = person(engine, chat)
    delivered, failing = telegram_message(engine, user), telegram_message(engine, user)

    claimed = client.post("/internal/v1/outbox/claim", json={"limit": BATCH_MAX}, headers=bearer(token))
    sent = client.post(f"/internal/v1/outbox/{delivered}/sent", headers=bearer(token))
    failed = client.post(f"/internal/v1/outbox/{failing}/failed", headers=bearer(token))
    sent_again = client.post(f"/internal/v1/outbox/{delivered}/sent", headers=bearer(token))

    mine = [item for item in claimed.json()["items"] if item["chat_id"] == chat]
    assert claimed.status_code == 200
    assert [item["id"] for item in mine] == [delivered, failing]
    assert mine[0]["text"] == "Неожиданная ошибка сервера: KeyError в GET /web/. Номер ошибки E-AAAAAAAAAA."
    assert (sent.status_code, failed.status_code, sent_again.status_code) == (204, 204, 404)
    assert sql(engine, "SELECT attempts FROM notifications WHERE id = :n", n=failing) == 1
    assert sql(engine, "SELECT sent_at FROM notifications WHERE id = :n", n=delivered) == NOW


def test_service_token_never_logged(client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    token = service_token(engine)

    with caplog.at_level(logging.DEBUG):
        client.post("/internal/v1/outbox/claim", json={"limit": 1}, headers=bearer(token))
        client.post("/internal/v1/telegram/link", json={"code": "не тот", "chat_id": 1}, headers=bearer(token))

    assert token not in "\n".join(str(vars(record)) for record in caplog.records)
