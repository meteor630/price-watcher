"""Вход приложения по HTTP: ответы по схемам OpenAPI, только bearer в /api/v1, ротация, выход."""

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.api.web_auth import SESSION_COOKIE
from pricewatch.application.app_auth import issue_pairing_code
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.web_auth import login
from pricewatch.config.settings import Settings
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "app-http-test-key-" + "k" * 20
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PASSWORD = "правильный пароль 42"
HASHER = PasswordHasher(time_cost=1, memory_cost=8 * 1024)
OPENAPI = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi" / "v1.yaml").read_text(encoding="utf-8"))


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]})


TOKEN_PAIR, ME, ERROR = schema("TokenPair"), schema("Me"), schema("ErrorResponse")


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


def make_app(engine: Engine, clock: list[datetime]) -> FastAPI:
    settings = Settings(
        env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None
    )  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), HASHER, lambda: clock[0])
    return create_app(settings, context)


@pytest.fixture
def clock() -> list[datetime]:
    return [NOW]


@pytest.fixture
def client(engine: Engine, clock: list[datetime]) -> TestClient:
    return TestClient(make_app(engine, clock), base_url="https://testserver")


def add_user(engine: Engine) -> tuple[int, str]:
    name = f"m{uuid.uuid4().hex[:10]}"
    with engine.begin() as connection:
        user_id = connection.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, :h, 'user', 'Мария') RETURNING id"),
            {"l": name, "h": HASHER.hash(PASSWORD)},
        ).scalar_one()
    return user_id, name


def pair(client: TestClient, engine: Engine, user_id: int) -> dict:
    code = issue_pairing_code(SqlUnitOfWork(engine), KEY.encode(), user_id, NOW).code
    response = client.post("/api/v1/auth/pair", json={"code": code, "device_name": "iPad"})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def error_code(response) -> str:  # type: ignore[no-untyped-def]
    ERROR.validate(response.json())
    return response.json()["error"]["code"]


def test_pair_returns_token_pair_by_schema_without_caching(client: TestClient, engine: Engine) -> None:
    user_id, _ = add_user(engine)
    code = issue_pairing_code(SqlUnitOfWork(engine), KEY.encode(), user_id, NOW).code

    response = client.post("/api/v1/auth/pair", json={"code": code, "device_name": "iPad"})

    TOKEN_PAIR.validate(response.json())
    assert response.json()["token_type"] == "Bearer"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [
        {"code": "abcd2345", "device_name": "iPad"},
        {"code": "ABCD234", "device_name": "iPad"},
        {"code": "ABCD2345", "device_name": ""},
        {"code": "ABCD2345", "device_name": "iPad", "extra": 1},
        {"code": "ABCD2345"},
    ],
)
def test_pair_rejects_malformed_body(client: TestClient, body: dict) -> None:
    response = client.post("/api/v1/auth/pair", json=body)

    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"


def test_wrong_code_answers_by_schema(client: TestClient) -> None:
    response = client.post("/api/v1/auth/pair", json={"code": "ZZZZZZZZ", "device_name": "iPad"})

    assert response.status_code == 400
    assert error_code(response) == "PAIRING_CODE_INVALID"


def test_me_with_bearer(client: TestClient, engine: Engine) -> None:
    user_id, name = add_user(engine)
    tokens = pair(client, engine, user_id)

    response = client.get("/api/v1/me", headers=bearer(tokens["access_token"]))

    ME.validate(response.json())
    assert response.json() == {"id": user_id, "login": name, "display_name": "Мария", "role": "user"}
    assert response.headers["ETag"].startswith('"')


def test_me_without_token_has_bearer_challenge(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert error_code(response) == "AUTH_REQUIRED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer    ", "Token abc"])
def test_malformed_authorization_header(client: TestClient, header: str) -> None:
    assert client.get("/api/v1/me", headers={"Authorization": header}).status_code == 401


def web_session(engine: Engine, name: str) -> str:
    return login(SqlUnitOfWork(engine), HASHER, KEY.encode(), name, PASSWORD, None, NOW).token


def test_session_cookie_is_ignored_by_api(client: TestClient, engine: Engine) -> None:
    _, name = add_user(engine)
    client.cookies.set(SESSION_COOKIE, web_session(engine, name))

    response = client.get("/api/v1/me")

    assert response.status_code == 401


def test_cookie_and_bearer_together_use_only_bearer(client: TestClient, engine: Engine) -> None:
    cookie_user, cookie_login = add_user(engine)
    bearer_user, _ = add_user(engine)
    tokens = pair(client, engine, bearer_user)
    client.cookies.set(SESSION_COOKIE, web_session(engine, cookie_login))

    good = client.get("/api/v1/me", headers=bearer(tokens["access_token"]))
    bad = client.get("/api/v1/me", headers=bearer("made-up-token"))

    assert good.json()["id"] == bearer_user != cookie_user
    assert bad.status_code == 401  # действующая cookie не спасает неверный bearer


def test_refresh_rotation_over_http(client: TestClient, engine: Engine, clock: list[datetime]) -> None:
    user_id, _ = add_user(engine)
    first = pair(client, engine, user_id)

    second = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    clock[0] = NOW.replace(minute=2)
    stolen = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})

    TOKEN_PAIR.validate(second.json())
    assert second.headers["Cache-Control"] == "no-store"
    assert replay.json() == second.json()
    assert stolen.status_code == 401
    assert error_code(stolen) == "REFRESH_TOKEN_REUSED"
    assert client.get("/api/v1/me", headers=bearer(second.json()["access_token"])).status_code == 401


def test_logout_revokes_device(client: TestClient, engine: Engine) -> None:
    user_id, _ = add_user(engine)
    tokens = pair(client, engine, user_id)

    response = client.post("/api/v1/auth/logout", headers=bearer(tokens["access_token"]))

    assert response.status_code == 204
    assert client.get("/api/v1/me", headers=bearer(tokens["access_token"])).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_needs_bearer(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_pairing_rate_limit_over_http(engine: Engine, clock: list[datetime]) -> None:
    # Нужен настоящий адрес клиента: по умолчанию тестовый клиент представляется строкой «testclient».
    client = TestClient(make_app(engine, clock), base_url="https://testserver", client=("203.0.113.9", 50000))
    for _ in range(10):
        client.post("/api/v1/auth/pair", json={"code": "ZZZZZZZZ", "device_name": "X"})

    response = client.post("/api/v1/auth/pair", json={"code": "ZZZZZZZY", "device_name": "X"})

    assert response.status_code == 429
    assert response.json()["error"]["retry_after_seconds"] == 900


def test_tokens_never_logged(client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    user_id, _ = add_user(engine)
    with caplog.at_level(logging.DEBUG):
        tokens = pair(client, engine, user_id)
        client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        client.get("/api/v1/me", headers=bearer(tokens["access_token"]))

    logged = "\n".join(str(vars(record)) for record in caplog.records)
    assert tokens["access_token"] not in logged
    assert tokens["refresh_token"] not in logged


def test_token_times_are_utc_on_first_issue_and_replay(client: TestClient, engine: Engine) -> None:
    user_id, _ = add_user(engine)
    first = pair(client, engine, user_id)
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()

    for body in (first, second, replay):
        assert body["access_expires_at"].endswith("+00:00")
        assert body["refresh_expires_at"].endswith("+00:00")
