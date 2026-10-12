"""GET /healthz для Docker и сторожа: без входа, 200 — база отвечает, 503 DB_ERROR — нет; наружу ничего, кроме статуса."""

import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.passwords import PasswordHasher
from pricewatch.config.settings import Settings
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "health-test-key-" + "k" * 30


def client_for(engine: Engine) -> TestClient:
    settings = Settings(env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), PasswordHasher(time_cost=1, memory_cost=8192), lambda: datetime.now(UTC),
                         MediaStore(Path(tempfile.mkdtemp())), LinkCatalog())
    return TestClient(create_app(settings, context), base_url="https://testserver")


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


def test_healthy_without_login(engine: Engine) -> None:
    response = client_for(engine).get("/healthz")

    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store" and "set-cookie" not in response.headers


def test_database_down_is_503_without_details() -> None:
    dead = create_db_engine("postgresql+psycopg://nobody@127.0.0.1:9/none?connect_timeout=1")
    try:
        response = client_for(dead).get("/healthz")
    finally:
        dead.dispose()

    assert response.status_code == 503 and response.json()["error"]["code"] == "DB_ERROR"
    assert "127.0.0.1" not in response.text and "nobody" not in response.text


def test_only_get(engine: Engine) -> None:
    assert client_for(engine).post("/healthz").status_code == 405
