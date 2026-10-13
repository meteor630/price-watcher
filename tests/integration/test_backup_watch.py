"""Сторож копий на настоящей базе: неудачная попытка — одно уведомление на попытку, нет удачной копии дольше 36 часов —
одно на эпизод, в колокольчик и в Telegram администраторам; повтор обхода не повторяет уведомление, два сторожа
одновременно не сообщают дважды; без попыток копии не настроены — молчание."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.backups import BackupAlerts, watch_backups
from pricewatch.application.notifications import bell_page
from pricewatch.application.ports import BackupOutcome
from pricewatch.domain.accounts import Role, User
from pricewatch.domain.backups import STALE_AFTER
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, notifications, backup_runs CASCADE"))


def admin(engine: Engine, chat: int | None = None) -> User:
    with engine.begin() as db:
        user_id = db.execute(text(
            "INSERT INTO users (login, password_hash, role, display_name, telegram_chat_id) "
            "VALUES ('a' || substr(md5(random()::text), 1, 10), 'h', 'admin', 'A', :c) RETURNING id"
        ), {"c": chat}).scalar_one()
    return User(user_id, "a", Role.ADMIN, "A", False)  # type: ignore[arg-type]


def attempt(engine: Engine, started: datetime, outcome: BackupOutcome | None) -> int:
    with SqlUnitOfWork(engine) as uow:
        backup_id = uow.backups.start(started)
        if outcome is not None:
            uow.backups.finish(backup_id, outcome, started + timedelta(minutes=1))
        uow.commit()
    return backup_id


OK = BackupOutcome("ok", "pricewatch-x.tar.age", 100, None)
UPLOAD_FAILED = BackupOutcome("failed", "pricewatch-y.tar.age", 100, "UPLOAD_FAILED")


def notes(engine: Engine) -> list[tuple[str, str, int]]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(text("SELECT kind, channel, repeat_count FROM notifications ORDER BY id"))]  # type: ignore[misc]


def watch(engine: Engine, now: datetime = NOW) -> BackupAlerts:
    return watch_backups(SqlUnitOfWork(engine), now)


# --- обычный путь ---


def test_fresh_successful_copy_is_quiet(engine: Engine) -> None:
    admin(engine, chat=111)
    attempt(engine, NOW - timedelta(hours=5), OK)
    assert watch(engine) == BackupAlerts()
    assert notes(engine) == []


def test_failed_copy_alerts_admins_once_in_bell_and_telegram(engine: Engine) -> None:
    owner = admin(engine, chat=111)
    admin(engine)
    attempt(engine, NOW - timedelta(days=1), OK)
    failed = attempt(engine, NOW - timedelta(hours=4, minutes=30), UPLOAD_FAILED)

    assert watch(engine) == BackupAlerts(failed_backup_id=failed)
    assert watch(engine, NOW + timedelta(seconds=30)) == BackupAlerts()
    assert notes(engine) == [("backup_failed", "in_app", 1), ("backup_failed", "telegram", 1), ("backup_failed", "in_app", 1)]
    (item,) = bell_page(SqlUnitOfWork(engine), owner, 0, 10).items
    assert item.text == "Резервная копия 14.10 в 07:30 не удалась: копия сделана, но не отправлена на арендный сервер. Журнал — служба backup на домашнем узле."


def test_each_failed_attempt_is_its_own_alert(engine: Engine) -> None:
    admin(engine)
    attempt(engine, NOW - timedelta(days=2), BackupOutcome("failed", None, None, "DATABASE_DUMP_FAILED"))
    watch(engine, NOW - timedelta(days=2) + timedelta(hours=1))
    attempt(engine, NOW - timedelta(days=1), BackupOutcome("failed", None, None, "DATABASE_DUMP_FAILED"))
    watch(engine, NOW - timedelta(days=1) + timedelta(hours=1))
    assert [kind for kind, _, _ in notes(engine)] == ["backup_failed", "backup_failed"]


# --- граница: 36 часов и новые эпизоды ---


def test_no_success_for_more_than_36_hours_alerts_once_per_episode(engine: Engine) -> None:
    owner = admin(engine)
    attempt(engine, NOW - STALE_AFTER - timedelta(hours=1), OK)

    first = watch(engine)
    assert first.stale_since is not None
    assert watch(engine, NOW + timedelta(hours=1)) == BackupAlerts()
    (item,) = bell_page(SqlUnitOfWork(engine), owner, 0, 10).items
    assert item.text.startswith("Удачной резервной копии нет с 12.10 в ")

    # Новая удачная копия — эпизод закончился; следующее молчание — новое уведомление.
    attempt(engine, NOW + timedelta(hours=2), OK)
    assert watch(engine, NOW + timedelta(hours=3)) == BackupAlerts()
    assert watch(engine, NOW + timedelta(hours=2) + STALE_AFTER + timedelta(minutes=2)).stale_since is not None
    assert [kind for kind, _, _ in notes(engine)] == ["backup_stale", "backup_stale"]


def test_exactly_36_hours_without_success_is_quiet(engine: Engine) -> None:
    admin(engine)
    attempt(engine, NOW - STALE_AFTER - timedelta(minutes=1), OK)  # закончилась через минуту — ровно 36 часов назад
    assert watch(engine) == BackupAlerts()


def test_never_successful_counts_from_first_attempt(engine: Engine) -> None:
    owner = admin(engine)
    attempt(engine, NOW - timedelta(hours=40), None)  # оборванная попытка
    alerts = watch(engine)
    assert alerts == BackupAlerts(stale_since=NOW - timedelta(hours=40))
    (item,) = bell_page(SqlUnitOfWork(engine), owner, 0, 10).items
    assert item.text.startswith("Ни одной удачной резервной копии с первой попытки 12.10 в 20:00")


# --- отказ: нечего сторожить и гонка двух сторожей ---


def test_without_attempts_backups_are_not_set_up(engine: Engine) -> None:
    admin(engine)
    assert watch(engine) == BackupAlerts()
    assert notes(engine) == []


def test_two_watchers_at_once_alert_once(engine: Engine) -> None:
    admin(engine)
    failed = attempt(engine, NOW - timedelta(hours=1), UPLOAD_FAILED)
    results: list[BackupAlerts] = []
    start = threading.Barrier(2)

    def run() -> None:
        start.wait()
        results.append(watch(engine))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result.failed_backup_id or 0 for result in results) == [0, failed]
    assert notes(engine) == [("backup_failed", "in_app", 1)]
