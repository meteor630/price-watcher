"""Протокол worker по HTTP: только токен worker, строгие схемы, чужое задание не трогается, токен не в журнале; фото — недоверенный файл."""

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from PIL import Image
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.api.web_auth import SESSION_COOKIE
from pricewatch.application.app_auth import issue_pairing_code
from pricewatch.application.links import add_link
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.worker_protocol import create_worker
from pricewatch.config.settings import Settings
from pricewatch.media.images import MAX_INPUT_BYTES
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "worker-http-key-" + "k" * 30
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    with db_engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET status = 'cancelled', lease_until = NULL, finished_at = now() WHERE status IN ('queued', 'leased')"))
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    settings = Settings(env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), PasswordHasher(time_cost=1, memory_cost=8192), lambda: NOW, MediaStore(tmp_path))
    return TestClient(create_app(settings, context), base_url="https://testserver")


def worker_token(engine: Engine) -> str:
    return create_worker(SqlUnitOfWork(engine), KEY.encode(), f"w-{uuid.uuid4().hex[:8]}", NOW)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def queued_link(engine: Engine) -> int:
    with engine.begin() as db:
        user_id = db.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"), {"l": f"u{uuid.uuid4().hex[:10]}"}
        ).scalar_one()
        category = db.execute(text("INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, 'Дом', 'дом', 0) RETURNING id"), {"u": user_id}).scalar_one()
        item = db.execute(text("INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, 'Лампа', 0) RETURNING id"), {"u": user_id, "c": category}).scalar_one()
    added = add_link(SqlUnitOfWork(engine), LinkCatalog(), user_id, item, f"https://shop-{uuid.uuid4().hex[:8]}.example/p", "spb", NOW)
    assert added.task_id is not None
    return added.task_id


def claim(client: TestClient, token: str) -> dict:
    response = client.post("/worker/v1/claim", headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


ROUTES = [("/worker/v1/claim", None), ("/worker/v1/heartbeat", {"task_id": 1}), ("/worker/v1/result", {"task_id": 1, "status": "TIMEOUT"})]


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_every_worker_route_needs_worker_token(client: TestClient, path: str, body: dict | None) -> None:
    for headers in ({}, bearer("made-up"), {"Authorization": "Basic eDp5"}):
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_app_token_and_site_cookie_do_not_open_worker_routes(client: TestClient, engine: Engine) -> None:
    with engine.begin() as db:
        user_id = db.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'admin', 'A') RETURNING id"), {"l": f"a{uuid.uuid4().hex[:10]}"}
        ).scalar_one()
    code = issue_pairing_code(SqlUnitOfWork(engine), KEY.encode(), user_id, NOW).code
    tokens = client.post("/api/v1/auth/pair", json={"code": code, "device_name": "iPad"}).json()
    client.cookies.set(SESSION_COOKIE, "some-session")

    assert client.post("/worker/v1/claim", headers=bearer(tokens["access_token"])).status_code == 401
    assert client.post("/worker/v1/claim").status_code == 401


def test_claim_heartbeat_result_over_http(client: TestClient, engine: Engine) -> None:
    token = worker_token(engine)
    task_id = queued_link(engine)

    task = claim(client, token)["task"]
    beat = client.post("/worker/v1/heartbeat", json={"task_id": task["task_id"]}, headers=bearer(token))
    done = client.post(
        "/worker/v1/result",
        json={"task_id": task["task_id"], "status": "ok", "price_minor": 129_900, "availability": "in_stock", "title": "Лампа", "source": "site_data", "fetch_method": "http", "duration_ms": 900},
        headers=bearer(token),
    )

    assert task["task_id"] == task_id and task["url"].startswith("https://shop-") and task["lease_until"].endswith("+00:00")
    assert beat.status_code == 200 and beat.json()["lease_until"].endswith("+00:00")
    assert done.status_code == 204
    assert claim(client, token)["task"] is None


def test_someone_elses_task_is_not_found(client: TestClient, engine: Engine) -> None:
    owner, stranger = worker_token(engine), worker_token(engine)
    queued_link(engine)
    task_id = claim(client, owner)["task"]["task_id"]

    beat = client.post("/worker/v1/heartbeat", json={"task_id": task_id}, headers=bearer(stranger))
    result = client.post("/worker/v1/result", json={"task_id": task_id, "status": "TIMEOUT"}, headers=bearer(stranger))

    assert (beat.status_code, result.status_code) == (404, 404)


@pytest.mark.parametrize(
    "body",
    [
        {"task_id": "1", "status": "TIMEOUT"},  # строка вместо числа
        {"task_id": 1, "status": "ok", "price_minor": 1299.5, "availability": "in_stock", "source": "site_data"},  # дробная цена
        {"task_id": 1, "status": "ok", "price_minor": 0, "availability": "in_stock", "source": "site_data"},
        {"task_id": 1, "status": "TIMEOUT", "extra": 1},  # лишнее поле
        {"task_id": 1, "status": "ok", "price_minor": 100, "availability": "maybe", "source": "site_data"},
        {"task_id": 1, "status": "ok", "price_minor": 100, "availability": "in_stock", "source": "site_data", "title": "т" * 2000},
        {"task_id": 0, "status": "TIMEOUT"},
        {"status": "TIMEOUT"},
    ],
)
def test_malformed_result_is_refused(client: TestClient, engine: Engine, body: dict) -> None:
    response = client.post("/worker/v1/result", json=body, headers=bearer(worker_token(engine)))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_worker_token_never_logged(client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    token = worker_token(engine)
    with caplog.at_level(logging.DEBUG):
        client.post("/worker/v1/claim", headers=bearer(token))
        client.post("/worker/v1/heartbeat", json={"task_id": 999_999}, headers=bearer(token))

    assert token not in "\n".join(str(vars(record)) for record in caplog.records)


# --- фото ---


def picture(fmt: str = "WEBP", size: tuple[int, int] = (40, 30)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, "orange").save(out, fmt)
    return out.getvalue()


def upload(client: TestClient, token: str, task_id: int, data: bytes, content_type: str = "image/webp") -> Response:
    return client.post("/worker/v1/image", params={"task_id": task_id}, content=data, headers=bearer(token) | {"Content-Type": content_type})


def image_path(engine: Engine, task_id: int) -> str | None:
    with engine.connect() as db:
        return db.execute(
            text("SELECT l.image_path FROM tracked_links l JOIN check_tasks t ON t.tracked_link_id = l.id WHERE t.id = :t"), {"t": task_id}
        ).scalar_one()


def test_image_is_stored_and_link_no_longer_needs_one(client: TestClient, engine: Engine, tmp_path: Path) -> None:
    token = worker_token(engine)
    queued_link(engine)
    task = claim(client, token)["task"]
    assert task["need_image"] is True

    response = upload(client, token, task["task_id"], picture())

    assert response.status_code == 204, response.text
    stored = image_path(engine, task["task_id"])
    assert stored is not None and (tmp_path / stored).is_file()
    with Image.open(tmp_path / stored) as saved:
        assert saved.format == "WEBP"
    assert upload(client, token, task["task_id"], picture(size=(50, 50))).status_code == 204
    assert image_path(engine, task["task_id"]) == stored  # первое фото остаётся


def test_image_route_needs_worker_token(client: TestClient) -> None:
    response = client.post("/worker/v1/image", params={"task_id": 1}, content=picture(), headers={"Content-Type": "image/webp"})

    assert response.status_code == 401


def test_image_for_someone_elses_task_is_not_found(client: TestClient, engine: Engine) -> None:
    owner, stranger = worker_token(engine), worker_token(engine)
    queued_link(engine)
    task_id = claim(client, owner)["task"]["task_id"]

    assert upload(client, stranger, task_id, picture()).status_code == 404
    assert image_path(engine, task_id) is None


@pytest.mark.parametrize(
    ("data", "content_type", "reason"),
    [
        (picture(), "image/png", "content_type"),
        (picture("JPEG"), "image/webp", "unreadable"),  # чужой формат под видом WebP
        (b"<svg/>", "image/webp", "unreadable"),
        (b"\0" * (MAX_INPUT_BYTES + 1), "image/webp", "too_large"),
    ],
    ids=["тип", "jpeg", "svg", "больше 2 МБ"],
)
def test_bad_image_is_refused(client: TestClient, engine: Engine, tmp_path: Path, data: bytes, content_type: str, reason: str) -> None:
    token = worker_token(engine)
    queued_link(engine)
    task_id = claim(client, token)["task"]["task_id"]

    response = upload(client, token, task_id, data, content_type)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert image_path(engine, task_id) is None
    assert list(tmp_path.iterdir()) == []
