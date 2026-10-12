"""Сторож на настоящей базе: молчание дольше 5 минут — одно уведомление на инцидент, восстановление — одно сообщение,
бот — только колокольчик, новое молчание — новый инцидент, два сторожа одновременно не сообщают дважды."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.notifications import bell_page
from pricewatch.application.services import create_service
from pricewatch.application.watchdog import SILENCE_LIMIT, WatchReport, watch
from pricewatch.application.worker_protocol import create_worker
from pricewatch.domain.accounts import Role, User
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"watchdog-key-" + b"k" * 32
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, workers, services, notifications CASCADE"))


def one(engine: Engine, sql: str, **params: object) -> object:
    with engine.begin() as db:
        return db.execute(text(sql), params).scalar_one()


def admin(engine: Engine, chat: int | None = None) -> User:
    user_id = one(
        engine, "INSERT INTO users (login, password_hash, role, display_name, telegram_chat_id) VALUES ('a' || substr(md5(random()::text), 1, 10), 'h', 'admin', 'A', :c) RETURNING id",
        c=chat,
    )
    return User(user_id, "a", Role.ADMIN, "A", False)  # type: ignore[arg-type]


def seen(engine: Engine, table: str, name: str, at: datetime | None) -> None:
    with engine.begin() as db:
        db.execute(text(f"UPDATE {table} SET last_seen_at = :t WHERE name = :n"), {"t": at, "n": name})


def notes(engine: Engine) -> list[tuple[str, str, int]]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(text("SELECT kind, channel, repeat_count FROM notifications ORDER BY id"))]  # type: ignore[misc]


def run(engine: Engine, now: datetime) -> WatchReport:
    return watch(SqlUnitOfWork(engine), now)


def test_silent_worker_is_reported_once_then_recovery(engine: Engine) -> None:
    boss = admin(engine, chat=900_001)
    create_worker(SqlUnitOfWork(engine), KEY, "home-pi", NOW)
    seen(engine, "workers", "home-pi", NOW - SILENCE_LIMIT - timedelta(seconds=1))

    first = run(engine, NOW)
    again = run(engine, NOW + timedelta(minutes=10))
    seen(engine, "workers", "home-pi", NOW + timedelta(minutes=11))
    back = run(engine, NOW + timedelta(minutes=11, seconds=30))
    quiet = run(engine, NOW + timedelta(minutes=12))

    assert first == WatchReport(silent=("worker:home-pi",)) and again == WatchReport()
    assert back == WatchReport(recovered=("worker:home-pi",)) and quiet == WatchReport()
    assert notes(engine) == [("worker_silent", "in_app", 1), ("worker_silent", "telegram", 1), ("worker_back", "in_app", 1), ("worker_back", "telegram", 1)]
    texts = [item.text for item in bell_page(SqlUnitOfWork(engine), boss, 0, 10).items]
    assert texts == ["Worker «home-pi» снова на связи (молчал с 12.10 в 14:54).", "Worker «home-pi» молчит с 12.10 в 14:54: цены не проверяются."]


def test_five_minutes_is_not_yet_silence_and_never_seen_is_not_silent(engine: Engine) -> None:
    admin(engine)
    create_worker(SqlUnitOfWork(engine), KEY, "on-time", NOW)
    create_worker(SqlUnitOfWork(engine), KEY, "never-started", NOW)
    seen(engine, "workers", "on-time", NOW - SILENCE_LIMIT)

    assert run(engine, NOW) == WatchReport()
    assert notes(engine) == []


def test_disabled_worker_is_not_watched(engine: Engine) -> None:
    admin(engine)
    create_worker(SqlUnitOfWork(engine), KEY, "retired", NOW)
    seen(engine, "workers", "retired", NOW - timedelta(hours=1))
    with engine.begin() as db:
        db.execute(text("UPDATE workers SET disabled_at = now()"))

    assert run(engine, NOW) == WatchReport()


def test_silent_bot_goes_only_to_the_bell(engine: Engine) -> None:
    admin(engine, chat=900_002)
    create_service(SqlUnitOfWork(engine), KEY, "telegram", NOW)
    seen(engine, "services", "telegram", NOW - timedelta(minutes=6))

    silent = run(engine, NOW)
    seen(engine, "services", "telegram", NOW + timedelta(minutes=1))
    back = run(engine, NOW + timedelta(minutes=1))

    assert silent == WatchReport(silent=("bot:telegram",)) and back == WatchReport(recovered=("bot:telegram",))
    assert notes(engine) == [("bot_silent", "in_app", 1), ("bot_back", "in_app", 1)]


def test_second_silence_is_a_new_incident_with_telegram(engine: Engine) -> None:
    admin(engine, chat=900_003)
    create_worker(SqlUnitOfWork(engine), KEY, "home-pi", NOW)
    for start in (NOW, NOW + timedelta(days=1)):
        seen(engine, "workers", "home-pi", start - timedelta(minutes=6))
        run(engine, start)
        seen(engine, "workers", "home-pi", start + timedelta(minutes=1))
        run(engine, start + timedelta(minutes=1))

    assert len(notes(engine)) == 8 and all(repeats == 1 for _, _, repeats in notes(engine))
    assert one(engine, "SELECT count(*) FROM notifications WHERE channel = 'telegram' AND kind = 'worker_silent' AND sent_at IS NULL") == 2


def test_no_admins_still_closes_the_incident(engine: Engine) -> None:
    create_worker(SqlUnitOfWork(engine), KEY, "home-pi", NOW)
    seen(engine, "workers", "home-pi", NOW - timedelta(minutes=6))

    assert run(engine, NOW) == WatchReport(silent=("worker:home-pi",))
    assert one(engine, "SELECT silent_since FROM workers WHERE name = 'home-pi'") == NOW - timedelta(minutes=6)
    assert notes(engine) == []


def test_two_watchdogs_at_once_report_once(engine: Engine) -> None:
    admin(engine)
    create_worker(SqlUnitOfWork(engine), KEY, "home-pi", NOW)
    seen(engine, "workers", "home-pi", NOW - timedelta(minutes=6))
    reports: list[WatchReport] = []
    start = threading.Barrier(2)

    def watchdog() -> None:
        start.wait()
        reports.append(run(engine, NOW))

    threads = [threading.Thread(target=watchdog) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(len(report.silent) for report in reports) == [0, 1]
    assert notes(engine) == [("worker_silent", "in_app", 1)]
