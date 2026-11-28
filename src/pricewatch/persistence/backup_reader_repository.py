"""Домашние приёмники копий (backup_readers): токен только на чтение копий, отметка последнего получения."""

from datetime import datetime

from sqlalchemy import Table, func, select, update

from pricewatch.application.ports import PullHealth
from pricewatch.persistence.collection_repositories import SqlWorkerRepository
from pricewatch.persistence.models import BackupReaderRow

backup_readers: Table = BackupReaderRow.__table__


class SqlBackupReaderRepository(SqlWorkerRepository):
    """Тот же механизм токена, что у worker, в своей таблице; пульс молчания сторожа здесь не используется."""

    table = backup_readers
    name_index = "uq_backup_readers_name"

    def mark_pulled(self, reader_id: int, now: datetime) -> None:
        self.connection.execute(update(backup_readers).where(backup_readers.c.id == reader_id).values(last_pulled_at=now))

    def pull_health(self) -> PullHealth | None:
        row = self.connection.execute(
            select(func.max(backup_readers.c.last_pulled_at), func.min(backup_readers.c.created_at), func.count())
            .where(backup_readers.c.disabled_at.is_(None))
        ).one()
        if row[2] == 0:
            return None
        return PullHealth(row[0], row[1])
