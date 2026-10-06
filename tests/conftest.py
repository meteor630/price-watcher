"""Общие фикстуры: временные базы PostgreSQL.

TEST_DATABASE_URL — адрес сервера, где можно создавать базы, например
postgresql://localhost/postgres. Без него тесты базы падают с объяснением:
пропуск проверки был бы её ослаблением.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command
from sqlalchemy import URL, create_engine, make_url, text

from pricewatch.persistence.schema import alembic_config

TEST_DATABASE_ENV = "TEST_DATABASE_URL"


def _server_url() -> URL:
    admin_url = os.environ.get(TEST_DATABASE_ENV)
    if not admin_url:
        pytest.fail(
            f"Для тестов базы задайте {TEST_DATABASE_ENV}, например "
            f"{TEST_DATABASE_ENV}=postgresql://localhost/postgres uv run pytest"
        )
    return make_url(admin_url).set(drivername="postgresql+psycopg")


@contextmanager
def temporary_database() -> Iterator[str]:
    """Новая пустая база с уникальным именем; удаляется при выходе, даже если занята."""
    server = _server_url()
    name = f"pricewatch_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield server.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Пустая база на весь прогон."""
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    """База со схемой последней миграции на весь прогон."""
    with temporary_database() as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    """Отдельная пустая база на один тест — для проверок самих миграций."""
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="module")
def module_database_url() -> Iterator[str]:
    """Своя база со схемой на модуль — для тестов, которым мешают строки других модулей (например, идущий запуск один на всех)."""
    with temporary_database() as url:
        command.upgrade(alembic_config(url), "head")
        yield url
