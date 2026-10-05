"""Подключение к PostgreSQL: работает, пароль со спецсимволами, ошибки без пароля в журнале."""

import io
import logging
import socket
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import Engine, make_url, text
from sqlalchemy.exc import SQLAlchemyError

from pricewatch.config.settings import load_settings
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.observability.json_log import JsonFormatter
from pricewatch.persistence.database import create_db_engine, server_version

SPECIAL_PASSWORD = "p@ss:w/rd%#?&=" + uuid.uuid4().hex[:6]


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    import os

    for name in list(os.environ):
        if name.startswith("PRICEWATCH_"):
            monkeypatch.delenv(name)
    return monkeypatch


def settings_url(monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path) -> str:
    """Адрес проходит через load_settings, как в рабочем процессе: пароль регистрируется для журнала."""
    monkeypatch.setenv("PRICEWATCH_ENV", "test")
    monkeypatch.setenv("PRICEWATCH_PUBLIC_ORIGIN", "http://localhost:8000")
    monkeypatch.setenv("PRICEWATCH_DATABASE_URL", database_url)
    monkeypatch.setenv("PRICEWATCH_SECRET_KEY", "test-secret-key-" + "k" * 32)
    return load_settings(tmp_path / ".env").database_url.get_secret_value()


def logged_failure(engine: Engine) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("pricewatch.test.db")
    logger.addHandler(handler)
    try:
        with pytest.raises(PriceWatcherError) as caught:
            server_version(engine)
        logger.error("База недоступна", exc_info=caught.value)
    finally:
        logger.removeHandler(handler)
    assert caught.value.code is ErrorCode.DB_ERROR
    return stream.getvalue()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_connects_to_postgres_18(engine: Engine) -> None:
    assert server_version(engine).split(".")[0] == "18"


def test_password_with_special_characters(engine: Engine, database_url: str) -> None:
    role = f"pw_test_{uuid.uuid4().hex[:8]}"
    escaped = SPECIAL_PASSWORD.replace("'", "''")
    with engine.connect() as connection:
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{escaped}'"))
        connection.commit()
    try:
        url = make_url(database_url).set(username=role, password=SPECIAL_PASSWORD)
        role_engine = create_db_engine(url.render_as_string(hide_password=False))
        try:
            assert server_version(role_engine).startswith("18")
        finally:
            role_engine.dispose()
    finally:
        with engine.connect() as connection:
            connection.execute(text(f"DROP ROLE {role}"))
            connection.commit()


def test_rejected_login_does_not_leak_password(
    clean_environment: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    bad = make_url(database_url).set(username=f"absent_{uuid.uuid4().hex[:8]}", password=SPECIAL_PASSWORD)
    url = settings_url(clean_environment, bad.render_as_string(hide_password=False), tmp_path)
    engine = create_db_engine(url)
    try:
        output = logged_failure(engine)
    finally:
        engine.dispose()

    assert "Traceback" in output
    assert SPECIAL_PASSWORD not in output
    assert quote(SPECIAL_PASSWORD, safe="") not in output


def test_unreachable_server_fails_fast_without_password(
    clean_environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = f"postgresql://pricewatch:{quote(SPECIAL_PASSWORD, safe='')}@127.0.0.1:{free_port()}/pricewatch"
    engine = create_db_engine(settings_url(clean_environment, url, tmp_path))
    try:
        output = logged_failure(engine)
    finally:
        engine.dispose()

    assert SPECIAL_PASSWORD not in output
    assert quote(SPECIAL_PASSWORD, safe="") not in output


def test_engine_repr_masks_password(clean_environment: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = f"postgresql://pricewatch:{quote(SPECIAL_PASSWORD, safe='')}@127.0.0.1:5432/pricewatch"
    engine = create_db_engine(url)

    assert SPECIAL_PASSWORD not in repr(engine) and quote(SPECIAL_PASSWORD, safe="") not in repr(engine)
    assert SPECIAL_PASSWORD not in str(engine.url)
    engine.dispose()


def test_sqlalchemy_does_not_print_query_parameters(engine: Engine) -> None:
    # Ошибка не связана со значением, поэтому PostgreSQL его не цитирует; проверяется,
    # что SQLAlchemy сам не добавляет параметры в текст. Сервер может цитировать
    # неверное значение (например, при приведении типа) — поэтому секреты в SQL
    # передаются только хешами, а в журнале зарегистрированные секреты вырезаются.
    with engine.connect() as connection, pytest.raises(SQLAlchemyError) as caught:
        connection.execute(text("SELECT CAST(:value AS text), 1 / 0"), {"value": "param-" + SPECIAL_PASSWORD})

    assert "SQL parameters hidden" in str(caught.value)
    assert SPECIAL_PASSWORD not in str(caught.value)


def test_timestamps_come_back_in_utc(engine: Engine) -> None:
    # Регрессия: соединение работало в часовом поясе сервера PostgreSQL, и время из базы
    # приходило как +03:00, а посчитанное в коде — как +00:00. Контракт API требует UTC.
    from datetime import timedelta

    with engine.connect() as connection:
        moment = connection.execute(text("SELECT TIMESTAMPTZ '2026-10-05 15:00:00+03'")).scalar_one()

    assert moment.utcoffset() == timedelta(0)
    assert moment.isoformat() == "2026-10-05T12:00:00+00:00"
