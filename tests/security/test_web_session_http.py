"""Сессия сайта по HTTP: флаги cookie, только cookie в /web, Origin и CSRF, лимит по IP через прокси.

Маршруты тестовые — те, что на этапе 7 сделают настоящие страницы.
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi import Depends, FastAPI, Request, Response
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext, client_ip, get_context
from pricewatch.api.web_auth import CSRF_HEADER, SESSION_COOKIE, set_session_cookie, web_user
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.web_auth import SessionUser, login
from pricewatch.config.settings import Settings
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

ORIGIN = "https://prices.example.ru"
KEY = "http-session-test-key-" + "k" * 16
PASSWORD = "правильный пароль 42"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
OPENAPI = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi" / "v1.yaml").read_text(encoding="utf-8"))
ERROR_SCHEMA = Draft202012Validator(OPENAPI["components"]["schemas"]["ErrorResponse"])
HASHER = PasswordHasher(time_cost=1, memory_cost=8 * 1024)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


def make_app(engine: Engine) -> FastAPI:
    settings = Settings(
        env="test",
        public_origin=ORIGIN,
        database_url="postgresql://unused@localhost/unused",
        secret_key=KEY,
        trusted_proxies="10.8.0.2/32",
        _env_file=None,
    )  # type: ignore[call-arg]
    context = ApiContext(
        unit_of_work=lambda: SqlUnitOfWork(engine),
        secret_key=KEY.encode(),
        hasher=HASHER,
        clock=lambda: NOW,
    )
    app = create_app(settings, context)

    @app.post("/web/t/login")
    async def do_login(request: Request, response: Response) -> dict[str, str]:
        form = await request.json()
        ctx = get_context(request)
        issued = login(ctx.unit_of_work(), ctx.hasher, ctx.secret_key, form["login"], form["password"], client_ip(request), ctx.clock())
        set_session_cookie(response, issued.token)
        return {"csrf_token": issued.csrf_token}

    @app.get("/web/t/me")
    def me(user: SessionUser = Depends(web_user)) -> dict[str, str]:
        return {"login": user.user.login}

    @app.post("/web/t/change")
    def change(user: SessionUser = Depends(web_user)) -> dict[str, bool]:
        return {"changed": True}

    return app


@pytest.fixture
def client(engine: Engine) -> TestClient:
    # https: клиент, как и браузер, не возвращает cookie с флагом Secure по http.
    return TestClient(make_app(engine), base_url="https://testserver", client=("10.8.0.2", 40000))


@pytest.fixture
def account(engine: Engine) -> str:
    name = f"h{uuid.uuid4().hex[:10]}"
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, :h, 'user', 'U')"),
            {"l": name, "h": HASHER.hash(PASSWORD)},
        )
    return name


def ip() -> str:
    return f"203.0.113.{uuid.uuid4().int % 250 + 1}"


def sign_in(client: TestClient, name: str, password: str = PASSWORD, forwarded: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(
        "/web/t/login",
        json={"login": name, "password": password},
        headers={"X-Forwarded-For": forwarded or ip()},
    )


def error_code(response) -> str:  # type: ignore[no-untyped-def]
    ERROR_SCHEMA.validate(response.json())
    return response.json()["error"]["code"]


def test_session_cookie_flags(client: TestClient, account: str) -> None:
    response = sign_in(client, account)

    cookie = response.headers["set-cookie"]
    assert response.status_code == 200
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=2592000"):
        assert flag in cookie
    token = client.cookies[SESSION_COOKIE]
    assert token not in response.text


def test_requests_without_session_are_refused(client: TestClient) -> None:
    response = client.get("/web/t/me")

    assert response.status_code == 401
    assert error_code(response) == "AUTH_REQUIRED"


def test_session_cookie_authenticates(client: TestClient, account: str) -> None:
    sign_in(client, account)

    assert client.get("/web/t/me").json() == {"login": account}


def test_bearer_is_ignored_on_web(client: TestClient, account: str) -> None:
    sign_in(client, account)
    token = client.cookies[SESSION_COOKIE]
    client.cookies.clear()

    response = client.get("/web/t/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert error_code(response) == "AUTH_REQUIRED"


def test_mutating_request_with_csrf_and_origin(client: TestClient, account: str) -> None:
    csrf = sign_in(client, account).json()["csrf_token"]

    response = client.post("/web/t/change", headers={CSRF_HEADER: csrf, "Origin": ORIGIN})

    assert response.json() == {"changed": True}


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": ORIGIN},  # нет CSRF-токена
        {CSRF_HEADER: "0" * 64, "Origin": ORIGIN},  # чужой токен
        {"Origin": "https://evil.example"},  # чужой Origin
        {"Origin": "null"},
        {},  # нет Origin
    ],
)
def test_mutating_request_without_valid_csrf_or_origin(client: TestClient, account: str, headers: dict) -> None:
    csrf = sign_in(client, account).json()["csrf_token"]
    # Для проверок Origin подкладывается верный CSRF-токен: отказ должен быть именно из-за Origin.
    if CSRF_HEADER not in headers and headers.get("Origin") != ORIGIN:
        headers = headers | {CSRF_HEADER: csrf}

    response = client.post("/web/t/change", headers=headers)

    assert response.status_code == 403
    assert error_code(response) == "CSRF_FAILED"


def test_csrf_of_other_session_is_rejected(engine: Engine, account: str) -> None:
    first, second = (TestClient(make_app(engine), base_url="https://testserver") for _ in range(2))
    sign_in(first, account)
    other_csrf = sign_in(second, account).json()["csrf_token"]

    response = first.post("/web/t/change", headers={CSRF_HEADER: other_csrf, "Origin": ORIGIN})

    assert error_code(response) == "CSRF_FAILED"


def test_safe_methods_do_not_need_csrf(client: TestClient, account: str) -> None:
    sign_in(client, account)

    assert client.get("/web/t/me", headers={"Origin": "https://evil.example"}).status_code == 200


def test_rate_limit_answer_and_retry_after(client: TestClient, account: str) -> None:
    for _ in range(5):
        assert sign_in(client, account, "неверный").status_code == 401

    response = sign_in(client, account)

    assert response.status_code == 429
    assert error_code(response) == "RATE_LIMITED"
    assert response.json()["error"]["retry_after_seconds"] == 900
    assert response.headers["Retry-After"] == "900"


def test_ip_limit_uses_visitor_ip_from_trusted_proxy(client: TestClient, account: str) -> None:
    attacker = ip()
    for _ in range(20):
        sign_in(client, f"guess{uuid.uuid4().hex[:6]}", "неверный", forwarded=f"1.1.1.1, {attacker}")

    blocked = sign_in(client, account, forwarded=attacker)
    other_visitor = sign_in(client, account, forwarded=ip())

    assert error_code(blocked) == "RATE_LIMITED"
    assert other_visitor.status_code == 200


def test_session_token_and_password_never_logged(client: TestClient, account: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        csrf = sign_in(client, account).json()["csrf_token"]
        token = client.cookies[SESSION_COOKIE]
        client.post("/web/t/change", headers={CSRF_HEADER: csrf, "Origin": ORIGIN})

    logged = "\n".join(str(vars(record)) for record in caplog.records)
    for secret in (token, csrf, PASSWORD):
        assert secret not in logged
