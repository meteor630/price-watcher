"""Сбои базы: недоступность, обрыв, взаимная блокировка → DB_ERROR (503); ошибки запросов не маскируются."""

import logging
import tempfile
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext, get_context
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.application.passwords import PasswordHasher
from pricewatch.config.settings import Settings
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.media.store import MediaStore
from pricewatch.observability.redaction import register_secret
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

PASSWORD = "db-fail-" + uuid.uuid4().hex[:8] + "@:/"
UNREACHABLE = f"postgresql://pricewatch:{quote(PASSWORD, safe='')}@127.0.0.1:1/pricewatch"


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def unreachable() -> Iterator[Engine]:
    db_engine = create_db_engine(UNREACHABLE)
    yield db_engine
    db_engine.dispose()


def test_unreachable_database_on_enter(unreachable: Engine) -> None:
    with pytest.raises(PriceWatcherError) as caught, SqlUnitOfWork(unreachable):
        pass

    assert caught.value.code is ErrorCode.DB_ERROR
    assert isinstance(caught.value.__cause__, OperationalError)


def test_connection_killed_mid_transaction(engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pricewatch.persistence.unit_of_work"), pytest.raises(PriceWatcherError) as caught:
        with SqlUnitOfWork(engine) as work:
            pid = work.users.connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            with engine.connect() as killer:
                killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            work.users.get(1)

    assert caught.value.code is ErrorCode.DB_ERROR
    with SqlUnitOfWork(engine) as work:  # пул выбросил мёртвое соединение и работает дальше
        assert work.users.connection.execute(text("SELECT 1")).scalar_one() == 1


def test_deadlock_becomes_db_error_for_one_side(engine: Engine) -> None:
    with engine.begin() as connection:
        user_id = connection.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"),
            {"l": f"d{uuid.uuid4().hex[:10]}"},
        ).scalar_one()
        ids = [
            connection.execute(
                text("INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, :n, :n, 0) RETURNING id"),
                {"u": user_id, "n": name},
            ).scalar_one()
            for name in ("первая", "вторая")
        ]
    both_locked_first = threading.Barrier(2)

    def lock(first: int, second: int) -> ErrorCode | None:
        try:
            with SqlUnitOfWork(engine) as work:
                bump = text("UPDATE categories SET version = version + 1 WHERE id = :id")
                work.categories.connection.execute(bump, {"id": first})
                both_locked_first.wait(10)
                work.categories.connection.execute(bump, {"id": second})
                work.commit()
            return None
        except PriceWatcherError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda pair: lock(*pair), [(ids[0], ids[1]), (ids[1], ids[0])]), key=str)

    assert outcomes == [ErrorCode.DB_ERROR, None]


def test_query_errors_are_not_disguised_as_db_failure(engine: Engine) -> None:
    with pytest.raises(ProgrammingError), SqlUnitOfWork(engine) as work:
        work.users.connection.execute(text("SELECT no_such_column FROM users"))


def test_db_failure_over_http_is_503_with_logged_cause(unreachable: Engine, caplog: pytest.LogCaptureFixture) -> None:
    register_secret(PASSWORD)  # как это делают настройки при загрузке
    settings = Settings(
        env="test", public_origin="https://prices.example.ru", database_url=UNREACHABLE, secret_key="k" * 40, _env_file=None
    )  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(unreachable), b"k" * 40, PasswordHasher(time_cost=1, memory_cost=8192), lambda: datetime.now(UTC), MediaStore(Path(tempfile.mkdtemp())), LinkCatalog())
    app = create_app(settings, context)

    @app.get("/t/db")
    def touch_db(ctx: ApiContext = Depends(get_context)) -> dict[str, bool]:
        with ctx.unit_of_work():
            return {"ok": True}

    with caplog.at_level(logging.ERROR):
        response = TestClient(app, raise_server_exceptions=False).get("/t/db")

    body = response.json()["error"]
    assert response.status_code == 503
    assert body["code"] == "DB_ERROR"
    assert body["error_id"]
    (record,) = [r for r in caplog.records if getattr(r, "error_id", None) == body["error_id"]]
    assert record.exc_info is not None
    from pricewatch.observability.json_log import JsonFormatter

    logged = JsonFormatter().format(record)
    assert "OperationalError" in logged
    assert PASSWORD not in logged and quote(PASSWORD, safe="") not in logged
    assert PASSWORD not in response.text
