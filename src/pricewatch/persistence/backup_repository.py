"""Журнал резервных копий (backup_runs)."""

from datetime import datetime

from sqlalchemy import Connection, Table, func, insert, select, text, update

from pricewatch.application.ports import BackupHealth, BackupOutcome
from pricewatch.persistence.models import BackupRunRow

backup_runs: Table = BackupRunRow.__table__
# Ключ транзакционной блокировки сторожа копий (pg_advisory_xact_lock), «PWBK».
WATCH_LOCK = 0x5057424B


class SqlBackupRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def start(self, now: datetime) -> int:
        return self.connection.execute(
            insert(backup_runs).values(status="running", started_at=now).returning(backup_runs.c.id)
        ).scalar_one()

    def finish(self, backup_id: int, outcome: BackupOutcome, now: datetime) -> None:
        self.connection.execute(
            update(backup_runs)
            .where(backup_runs.c.id == backup_id, backup_runs.c.status == "running")
            .values(
                status=outcome.status, finished_at=now, file_name=outcome.file_name, size_bytes=outcome.size_bytes,
                error_code=outcome.error_code,
            )
        )

    def health(self) -> BackupHealth | None:
        first = self.connection.execute(select(func.min(backup_runs.c.started_at))).scalar_one()
        if first is None:
            return None
        latest = self.connection.execute(
            select(backup_runs.c.id, backup_runs.c.status, backup_runs.c.error_code, backup_runs.c.started_at)
            .where(backup_runs.c.status != "running")
            .order_by(backup_runs.c.started_at.desc(), backup_runs.c.id.desc())
            .limit(1)
        ).one_or_none()
        last_ok = self.connection.execute(select(func.max(backup_runs.c.finished_at)).where(backup_runs.c.status == "ok")).scalar_one()
        return BackupHealth(
            latest.id if latest else None, latest.status if latest else None, latest.error_code if latest else None,
            latest.started_at if latest else None, last_ok, first,
        )

    def lock_watch(self) -> None:
        self.connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WATCH_LOCK})
