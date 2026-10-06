"""Репозитории сбора цен: поведение, которое видно только на настоящем PostgreSQL."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.collection_repositories import SqlQueueRepository, SqlStoreRepository, SqlTrackedLinkRepository
from pricewatch.persistence.database import create_db_engine

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


def test_urgent_tasks_survive_generic_plan_of_prepared_statement(engine: Engine) -> None:
    # psycopg готовит запрос после 5 выполнений, PostgreSQL после ещё 5 переходит на общий план
    # с параметрами. Условие частичного индекса в ON CONFLICT с параметрами ($1, $2) он с индексом
    # не сопоставляет — 11-я постановка задания на одном соединении падала (06.10.2026).
    host = f"shop-{uuid.uuid4().hex[:8]}.example"
    with engine.connect() as connection:
        store = SqlStoreRepository(connection).get_or_create(LinkCatalog().resolve(f"https://{host}/p"), NOW)
        queue = SqlQueueRepository(connection)
        tasks = [
            queue.enqueue_on_add(SqlTrackedLinkRepository(connection).get_or_create(store.id, f"https://{host}/p{i}", "spb", NOW), NOW)
            for i in range(15)
        ]
        connection.rollback()

    assert len(set(tasks)) == 15
