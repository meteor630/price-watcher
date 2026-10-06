"""Ключи повтора на настоящей базе: занять, повторить, другое тело, выполняется, оборванный, срок, очистка."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.idempotency import ABANDONED_AFTER, KEY_LIFETIME, Replay, abandon, begin, finish, purge_expired
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
HASH_A, HASH_B = "a" * 64, "b" * 64


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def user(engine: Engine) -> int:
    with engine.begin() as db:
        return db.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"), {"l": f"i{uuid.uuid4().hex[:10]}"}
        ).scalar_one()


def work(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


def test_first_request_runs_then_replays(engine: Engine, user: int) -> None:
    assert begin(work(engine), user, "key-00001", HASH_A, NOW) is None
    finish(work(engine), user, "key-00001", 200, {"quantity": 3})

    assert begin(work(engine), user, "key-00001", HASH_A, NOW + timedelta(hours=1)) == Replay(200, {"quantity": 3})


def test_same_key_other_body_is_refused(engine: Engine, user: int) -> None:
    begin(work(engine), user, "key-00002", HASH_A, NOW)
    finish(work(engine), user, "key-00002", 200, {})

    with pytest.raises(PriceWatcherError) as caught:
        begin(work(engine), user, "key-00002", HASH_B, NOW)
    assert caught.value.code is ErrorCode.IDEMPOTENCY_KEY_REUSED


def test_running_request_blocks_repeat_until_abandoned(engine: Engine, user: int) -> None:
    begin(work(engine), user, "key-00003", HASH_A, NOW)

    with pytest.raises(PriceWatcherError) as caught:
        begin(work(engine), user, "key-00003", HASH_A, NOW + timedelta(seconds=5))
    assert caught.value.details == {"in_progress": True}
    assert begin(work(engine), user, "key-00003", HASH_A, NOW + ABANDONED_AFTER + timedelta(seconds=1)) is None  # оборванный освобождается


def test_failed_request_releases_key(engine: Engine, user: int) -> None:
    begin(work(engine), user, "key-00004", HASH_A, NOW)
    abandon(work(engine), user, "key-00004")

    assert begin(work(engine), user, "key-00004", HASH_B, NOW) is None


def test_answer_expires_after_a_day(engine: Engine, user: int) -> None:
    begin(work(engine), user, "key-00005", HASH_A, NOW)
    finish(work(engine), user, "key-00005", 200, {})

    assert begin(work(engine), user, "key-00005", HASH_B, NOW + KEY_LIFETIME + timedelta(seconds=1)) is None


def test_keys_are_per_user(engine: Engine, user: int) -> None:
    with engine.begin() as db:
        other = db.execute(text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'O') RETURNING id"), {"l": f"o{uuid.uuid4().hex[:10]}"}).scalar_one()
    begin(work(engine), user, "key-00006", HASH_A, NOW)

    assert begin(work(engine), other, "key-00006", HASH_B, NOW) is None


def test_purge_removes_only_old_keys(engine: Engine, user: int) -> None:
    begin(work(engine), user, "key-old-01", HASH_A, NOW - KEY_LIFETIME - timedelta(minutes=1))
    begin(work(engine), user, "key-new-01", HASH_A, NOW)

    purge_expired(work(engine), NOW)

    with engine.connect() as db:
        keys = {row[0] for row in db.execute(text("SELECT key FROM idempotency_keys WHERE user_id = :u"), {"u": user})}
    assert "key-old-01" not in keys and "key-new-01" in keys
