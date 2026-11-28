"""Выдача копий домашнему приёмнику по HTTP (/backups/v1): только токен приёмника (не worker, не бот, не приложение,
не cookie сайта); неверный токен — неудача по IP, после 10 за 15 минут — 429; только имена копий, без выхода из папки;
копия без файла суммы не выдаётся; подтверждение получения ставит отметку для сторожа; папка не задана — 404."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.backup_readers import BACKUP_FAILURES_PER_IP, create_backup_reader
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.services import create_service
from pricewatch.application.worker_protocol import create_worker
from pricewatch.config.settings import Settings
from pricewatch.domain.backups import backup_name
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "backups-http-key-" + "k" * 30
NOW = datetime(2026, 11, 28, 6, 0, tzinfo=UTC)
COPY = backup_name(NOW - timedelta(hours=2))
UNFINISHED = backup_name(NOW - timedelta(hours=1))
DIGEST = "b" * 64


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("DELETE FROM auth_failures WHERE kind = 'backup'"))


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / COPY).write_bytes(b"encrypted-copy")
    (backups / (COPY + ".sha256")).write_text(f"{DIGEST}  {COPY}\n", encoding="ascii")
    (backups / UNFINISHED).write_bytes(b"no checksum yet")
    (backups / "notes.txt").write_text("not a copy")
    (tmp_path / "secret.txt").write_text("outside")
    return backups


def make_client(engine: Engine, tmp_path: Path, backups_dir: Path | None) -> TestClient:
    settings = Settings(  # type: ignore[call-arg]
        env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, backups_dir=backups_dir, _env_file=None,
    )
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), PasswordHasher(time_cost=1, memory_cost=8192), lambda: NOW, MediaStore(tmp_path), LinkCatalog())
    return TestClient(create_app(settings, context), base_url="https://testserver", client=("203.0.113.40", 50000))


@pytest.fixture
def client(engine: Engine, tmp_path: Path, folder: Path) -> TestClient:
    return make_client(engine, tmp_path, folder)


def reader_token(engine: Engine) -> str:
    return create_backup_reader(SqlUnitOfWork(engine), KEY.encode(), f"home-{uuid.uuid4().hex[:8]}", NOW - timedelta(days=1))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- обычный путь ---


def test_reader_lists_only_finished_copies(engine: Engine, client: TestClient) -> None:
    response = client.get("/backups/v1/files", headers=bearer(reader_token(engine)))
    assert response.status_code == 200
    assert response.json() == {"items": [{"name": COPY, "size_bytes": len(b"encrypted-copy"), "sha256": DIGEST}]}


def test_reader_downloads_copy(engine: Engine, client: TestClient) -> None:
    response = client.get(f"/backups/v1/files/{COPY}", headers=bearer(reader_token(engine)))
    assert response.status_code == 200
    assert response.content == b"encrypted-copy"
    assert response.headers["content-type"] == "application/octet-stream"


def test_received_marks_pull_time(engine: Engine, client: TestClient) -> None:
    token = reader_token(engine)
    assert client.post("/backups/v1/received", json={"name": COPY}, headers=bearer(token)).status_code == 204
    with engine.connect() as db:
        pulled = db.execute(text("SELECT max(last_pulled_at) FROM backup_readers")).scalar_one()
    assert pulled == NOW


# --- граница: копия без суммы, чужие файлы, папка не задана ---


@pytest.mark.parametrize("name", [UNFINISHED, "notes.txt", "..%2Fsecret.txt", "%2E%2E%2Fsecret.txt", COPY + ".sha256"])
def test_only_finished_copies_by_exact_name_are_served(engine: Engine, client: TestClient, name: str) -> None:
    response = client.get(f"/backups/v1/files/{name}", headers=bearer(reader_token(engine)))
    assert response.status_code in {404, 422}
    assert b"outside" not in response.content and b"no checksum" not in response.content


def test_without_backups_dir_nothing_is_served(engine: Engine, tmp_path: Path) -> None:
    client = make_client(engine, tmp_path, None)
    assert client.get("/backups/v1/files", headers=bearer(reader_token(engine))).status_code == 404


def test_received_with_extra_field_or_unknown_copy_is_rejected(engine: Engine, client: TestClient) -> None:
    token = reader_token(engine)
    assert client.post("/backups/v1/received", json={"name": COPY, "delete": True}, headers=bearer(token)).status_code == 422
    missing = backup_name(NOW - timedelta(days=9))
    assert client.post("/backups/v1/received", json={"name": missing}, headers=bearer(token)).status_code == 404


# --- отказ: чужие токены, перебор ---


def test_other_tokens_do_not_open_backups(engine: Engine, client: TestClient) -> None:
    worker = create_worker(SqlUnitOfWork(engine), KEY.encode(), f"w-{uuid.uuid4().hex[:8]}", NOW)
    service = create_service(SqlUnitOfWork(engine), KEY.encode(), f"bot-{uuid.uuid4().hex[:8]}", NOW)
    for headers in ({}, bearer(worker), bearer(service), bearer("x" * 43)):
        assert client.get("/backups/v1/files", headers=headers).status_code == 401
        assert client.get(f"/backups/v1/files/{COPY}", headers=headers).status_code == 401


def test_reader_token_does_not_open_worker_or_internal_api(engine: Engine, client: TestClient) -> None:
    token = reader_token(engine)
    assert client.post("/worker/v1/claim", headers=bearer(token)).status_code == 401
    assert client.post("/internal/v1/outbox/claim", json={"limit": 1}, headers=bearer(token)).status_code == 401


def test_repeated_wrong_tokens_are_rate_limited(engine: Engine, client: TestClient) -> None:
    for _ in range(BACKUP_FAILURES_PER_IP):
        assert client.get("/backups/v1/files", headers=bearer("y" * 43)).status_code == 401
    limited = client.get("/backups/v1/files", headers=bearer(reader_token(engine)))
    assert limited.status_code == 429


def test_disabled_reader_is_refused(engine: Engine, client: TestClient) -> None:
    token = reader_token(engine)
    with engine.begin() as db:
        db.execute(text("UPDATE backup_readers SET disabled_at = now()"))
    assert client.get("/backups/v1/files", headers=bearer(token)).status_code == 401
