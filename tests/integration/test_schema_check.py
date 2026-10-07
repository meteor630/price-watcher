"""pricewatch.persistence.schema: ревизия кода и базы, проверка совпадения."""

import pytest
from alembic import command
from sqlalchemy import Engine, text

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.schema import (
    SchemaMismatch,
    alembic_config,
    check_schema,
    current_revision,
    head_revision,
)


def engine_for(url: str) -> Engine:
    return create_db_engine(url)


def test_head_is_latest_migration() -> None:
    assert head_revision() == "0007"


def test_one_revision_behind_is_reported(empty_database_url: str) -> None:
    command.upgrade(alembic_config(empty_database_url), "0006")
    engine = engine_for(empty_database_url)
    try:
        assert current_revision(engine) == "0006"
        with pytest.raises(SchemaMismatch, match="в ревизии 0006, код ожидает 0007"):
            check_schema(engine)
    finally:
        engine.dispose()


def test_migrated_database_matches_code(migrated_database_url: str) -> None:
    engine = engine_for(migrated_database_url)
    try:
        assert current_revision(engine) == head_revision()
        assert check_schema(engine) == head_revision()
    finally:
        engine.dispose()


def test_empty_database_is_behind(empty_database_url: str) -> None:
    engine = engine_for(empty_database_url)
    try:
        assert current_revision(engine) is None
        with pytest.raises(SchemaMismatch, match="пустая.*alembic upgrade head"):
            check_schema(engine)
    finally:
        engine.dispose()


def test_downgraded_database_is_behind(empty_database_url: str) -> None:
    command.upgrade(alembic_config(empty_database_url), "head")
    command.downgrade(alembic_config(empty_database_url), "base")
    engine = engine_for(empty_database_url)
    try:
        assert current_revision(engine) is None
        with pytest.raises(SchemaMismatch, match="пустая"):
            check_schema(engine)
    finally:
        engine.dispose()


def test_unknown_revision_is_reported(empty_database_url: str) -> None:
    engine = engine_for(empty_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('9999_future')"))
        with pytest.raises(SchemaMismatch, match="неизвестной этой версии кода"):
            check_schema(engine)
    finally:
        engine.dispose()


def test_unreachable_database_is_db_error() -> None:
    engine = create_db_engine("postgresql://pricewatch:x@127.0.0.1:1/pricewatch")
    try:
        with pytest.raises(PriceWatcherError) as caught:
            current_revision(engine)
        assert caught.value.code is ErrorCode.DB_ERROR
    finally:
        engine.dispose()
