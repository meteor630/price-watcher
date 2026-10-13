"""Резервная копия на настоящей базе: журнал копий, удаление копий старше 30 суток, сбои."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from pricewatch.application.backups import RETENTION_FAILED, make_backup
from pricewatch.application.ports import BackupError, BackupOutcome
from pricewatch.domain.backups import KEEP, backup_name
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 14, 1, 30, tzinfo=UTC)


class FolderInMemory:
    """Папка копий: имена и размеры файлов; сбой — заданный код."""

    def __init__(self, names: list[str], *, create_fails: str | None = None, remove_fails: bool = False, upload_fails: bool = False) -> None:
        self.files = dict.fromkeys(names, 1)
        self.create_fails = create_fails
        self.remove_fails = remove_fails
        self.upload_fails = upload_fails
        self.uploaded: list[list[str]] = []

    def create(self, name: str) -> int:
        if self.create_fails:
            raise BackupError(self.create_fails)
        self.files[name] = 4096
        return 4096

    def names(self) -> list[str]:
        return list(self.files)

    def remove(self, name: str) -> None:
        if self.remove_fails:
            raise BackupError("STORAGE_FAILED")
        del self.files[name]

    def upload(self) -> None:
        if self.upload_fails:
            raise BackupError("UPLOAD_FAILED")
        self.uploaded.append(sorted(self.files))


class Clock:
    """Каждый вызов — на минуту позже: начало и конец попытки различаются."""

    def __init__(self, start: datetime) -> None:
        self.moment = start - timedelta(minutes=1)

    def __call__(self) -> datetime:
        self.moment += timedelta(minutes=1)
        return self.moment


@pytest.fixture
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    with db_engine.begin() as db:
        db.execute(text("DELETE FROM backup_runs"))
    yield db_engine
    db_engine.dispose()


def run(engine: Engine, folder: FolderInMemory) -> BackupOutcome:
    return make_backup(lambda: SqlUnitOfWork(engine), folder, Clock(NOW))


def journal(engine: Engine) -> list[tuple]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(text(
            "SELECT status, started_at, finished_at, file_name, size_bytes, error_code FROM backup_runs ORDER BY id"
        ))]


OLD = backup_name(NOW - KEEP - timedelta(hours=1))
RECENT = backup_name(NOW - timedelta(days=1))


# --- обычный путь ---


def test_successful_backup_is_journaled_old_copies_removed_and_rest_uploaded(engine: Engine) -> None:
    folder = FolderInMemory([OLD, RECENT, "notes.txt"])
    outcome = run(engine, folder)
    name = backup_name(NOW)
    assert outcome == BackupOutcome("ok", name, 4096, None)
    assert sorted(folder.files) == sorted([RECENT, "notes.txt", name])
    # На сервер уходит то, что осталось после удаления старых: копия старше 30 суток туда не попадёт.
    assert folder.uploaded == [sorted([RECENT, "notes.txt", name])]
    assert journal(engine) == [("ok", NOW, NOW + timedelta(minutes=1), name, 4096, None)]


# --- отказ ---


def test_failed_backup_keeps_old_copies_uploads_nothing_and_journals_code(engine: Engine) -> None:
    folder = FolderInMemory([OLD, RECENT], create_fails="DATABASE_DUMP_FAILED")
    assert run(engine, folder) == BackupOutcome("failed", None, None, "DATABASE_DUMP_FAILED")
    assert sorted(folder.files) == sorted([OLD, RECENT])
    assert folder.uploaded == []
    assert journal(engine) == [("failed", NOW, NOW + timedelta(minutes=1), None, None, "DATABASE_DUMP_FAILED")]


def test_copy_made_but_old_not_removed_is_a_failure_and_still_uploaded(engine: Engine) -> None:
    folder = FolderInMemory([OLD], remove_fails=True)
    name = backup_name(NOW)
    assert run(engine, folder) == BackupOutcome("failed", name, 4096, RETENTION_FAILED)
    assert folder.uploaded == [sorted([OLD, name])]
    assert journal(engine) == [("failed", NOW, NOW + timedelta(minutes=1), name, 4096, RETENTION_FAILED)]


def test_copy_kept_at_home_when_upload_fails(engine: Engine) -> None:
    folder = FolderInMemory([RECENT], upload_fails=True)
    name = backup_name(NOW)
    assert run(engine, folder) == BackupOutcome("failed", name, 4096, "UPLOAD_FAILED")
    assert sorted(folder.files) == sorted([RECENT, name])
    assert journal(engine) == [("failed", NOW, NOW + timedelta(minutes=1), name, 4096, "UPLOAD_FAILED")]


def test_first_failure_wins_when_both_steps_fail(engine: Engine) -> None:
    folder = FolderInMemory([OLD], remove_fails=True, upload_fails=True)
    assert run(engine, folder).error_code == RETENTION_FAILED


# --- граница: оборванная попытка и повторное завершение ---


def test_interrupted_attempt_stays_running(engine: Engine) -> None:
    with SqlUnitOfWork(engine) as uow:
        uow.backups.start(NOW)
        uow.commit()
    assert journal(engine) == [("running", NOW, None, None, None, None)]


def test_finished_attempt_is_not_rewritten(engine: Engine) -> None:
    with SqlUnitOfWork(engine) as uow:
        backup_id = uow.backups.start(NOW)
        uow.backups.finish(backup_id, BackupOutcome("ok", "pricewatch-a.tar.age", 1, None), NOW)
        uow.backups.finish(backup_id, BackupOutcome("failed", None, None, "STORAGE_FAILED"), NOW + timedelta(hours=1))
        uow.commit()
    assert journal(engine) == [("ok", NOW, NOW, "pricewatch-a.tar.age", 1, None)]


@pytest.mark.parametrize(
    ("status", "file_name", "size_bytes", "error_code"),
    [("ok", None, None, None), ("failed", None, None, None), ("ok", "x", 1, "STORAGE_FAILED"), ("failed", None, None, "bad code")],
    ids=["ok-without-file", "failed-without-code", "ok-with-code", "code-format"],
)
def test_journal_rejects_inconsistent_outcome(engine: Engine, status: str, file_name: str | None, size_bytes: int | None, error_code: str | None) -> None:
    with pytest.raises(IntegrityError), SqlUnitOfWork(engine) as uow:
        backup_id = uow.backups.start(NOW)
        uow.backups.finish(backup_id, BackupOutcome(status, file_name, size_bytes, error_code), NOW)
