"""Колокольчик: /api/v1/notifications по схеме и только администратору, фрагмент на сайте, уведомление о неожиданной ошибке."""

import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine
from test_dashboard_http import NOW, OPENAPI, bearer, client, engine, run_sql, schema, user_with_list  # noqa: F401 — фикстуры модуля
from test_web_home import EVIL, HASHER, KEY, ORIGIN, no_inline_code

from pricewatch.api.app import create_app
from pricewatch.api.web_auth import CSRF_HEADER, SESSION_COOKIE
from pricewatch.api.context import ApiContext
from pricewatch.application.web_auth import csrf_token_for
from pricewatch.config.settings import Settings
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

LIST_SCHEMA = Draft202012Validator(
    OPENAPI["paths"]["/notifications"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] | {"components": OPENAPI["components"]}
)


def admin(engine: Engine) -> int:
    user = user_with_list(engine, items=0)
    run_sql(engine, "UPDATE users SET role = 'admin' WHERE id = :u RETURNING id", u=user)
    return user


def bell_row(engine: Engine, user: int, store: str = "Ozon") -> int:
    return run_sql(
        engine,
        "INSERT INTO notifications (user_id, channel, kind, dedup_key, payload_json, created_at) VALUES (:u, 'in_app', 'store_paused', :k, "
        "jsonb_build_object('store', CAST(:s AS text), 'host', 'ozon.ru', 'reason', 'BLOCKED', 'paused_until', '2026-10-07T06:00:00+00:00'), :t) RETURNING id",
        u=user, k=f"store_paused:{uuid.uuid4().hex}", s=store, t=NOW,
    )


def read(client: TestClient, headers: dict[str, str], body: object):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/notifications/read", json=body, headers=headers | {"Idempotency-Key": uuid.uuid4().hex})


def test_list_and_read_for_admin(client: TestClient, engine: Engine) -> None:
    user = admin(engine)
    headers = bearer(client, engine, user)
    first, second = bell_row(engine, user), bell_row(engine, user)

    listing = client.get("/api/v1/notifications", headers=headers)
    one = read(client, headers, {"ids": [first]})
    rest = read(client, headers, {"all": True})

    LIST_SCHEMA.validate(listing.json())
    assert listing.json()["unread_count"] == 2 and {item["id"] for item in listing.json()["items"]} == {first, second}
    assert (one.status_code, one.json(), rest.json()) == (200, {"unread_count": 1}, {"unread_count": 0})


@pytest.mark.parametrize("body", [{"ids": []}, {"ids": [1, 1]}, {"ids": [1], "all": True}, {"all": False}, {}, {"ids": ["1"]}])
def test_strict_read_body(client: TestClient, engine: Engine, body: object) -> None:
    assert read(client, bearer(client, engine, admin(engine)), body).status_code == 422


def test_user_is_forbidden(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, user_with_list(engine, items=0))

    assert client.get("/api/v1/notifications", headers=headers).status_code == 403
    assert read(client, headers, {"all": True}).status_code == 403


def test_bell_fragment_on_site(engine: Engine) -> None:
    site = TestClient(create_app(_settings(), _context(engine)), base_url="https://testserver")
    login = f"b{uuid.uuid4().hex[:10]}"
    user = run_sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, :h, 'admin', 'A') RETURNING id", l=login, h=HASHER.hash("пароль-для-теста"))
    bell_row(engine, user, store=EVIL)
    assert site.post("/web/login", data={"login": login, "password": "пароль-для-теста"}, headers={"Origin": ORIGIN}, follow_redirects=False).status_code == 303

    bell = site.get("/web/notifications/bell").text
    headers = {"HX-Request": "true", "Origin": ORIGIN, CSRF_HEADER: csrf_token_for(KEY.encode(), site.cookies[SESSION_COOKIE])}
    cleared = site.post("/web/notifications/read", headers=headers).text

    assert "data-unread" in bell and EVIL not in bell and "&lt;img" in bell
    no_inline_code(bell)
    assert "data-unread" not in cleared and " open" in cleared


def test_unexpected_error_notifies_admins(engine: Engine) -> None:
    user = admin(engine)
    app = create_app(_settings(), _context(engine))

    @app.get("/t/boom/{thing}")
    def boom(thing: int) -> None:
        raise RuntimeError("сломалось")

    response = TestClient(app, raise_server_exceptions=False).get("/t/boom/7")

    assert response.status_code == 500
    error_id = response.json()["error"]["error_id"]
    payload = run_sql(engine, "SELECT payload_json FROM notifications WHERE user_id = :u AND channel = 'in_app' AND kind = 'unexpected_error' "
                              "ORDER BY id DESC LIMIT 1", u=user)
    assert payload == {"error_id": error_id, "method": "GET", "route": "/t/boom/{thing}", "exception": "RuntimeError"}


def _settings() -> Settings:
    return Settings(env="test", public_origin=ORIGIN, database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]


def _context(engine: Engine) -> ApiContext:
    return ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), HASHER, lambda: NOW, MediaStore(Path(tempfile.mkdtemp())), LinkCatalog())
