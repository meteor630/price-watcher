"""Журнал резервных копий (backup_runs)."""

from datetime import datetime

from sqlalchemy import Connection, Table, insert, update

from pricewatch.application.ports import BackupOutcome
from pricewatch.persistence.models import BackupRunRow

backup_runs: Table = BackupRunRow.__table__


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
