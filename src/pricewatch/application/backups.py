"""Резервная копия на домашнем узле (DEVELOPMENT_ARCHITECTURE.md, раздел 11).

1. Начало попытки записывается в журнал копий своей транзакцией: оборванная копия остаётся в журнале «running».
2. Копия создаётся вне транзакции базы: у pg_dump свой согласованный снимок.
3. Удачная копия — домашние копии старше 30 суток удаляются, а оставшиеся, которых нет на арендном сервере, отправляются
   туда (не отправленная вчера уйдёт сегодня). Оба шага выполняются, даже если первый не удался: свежую копию важно
   отправить; итог — код первого сбоя, файл копии в журнале остаётся. Неудачная копия — ничего не удаляется и не
   отправляется: старые копии остаются, пока нет новой.
4. Итог — в журнал копий; по нему планировщик узнаёт о сбое и о давности последней удачной копии.
"""

from collections.abc import Callable
from datetime import datetime

from pricewatch.application.ports import BackupArchive, BackupError, BackupOutcome, UnitOfWork
from pricewatch.domain.backups import backup_name, expired

RETENTION_FAILED = "RETENTION_FAILED"


def make_backup(uow_factory: Callable[[], UnitOfWork], archive: BackupArchive, clock: Callable[[], datetime]) -> BackupOutcome:
    started = clock()
    with uow_factory() as uow:
        backup_id = uow.backups.start(started)
        uow.commit()
    name = backup_name(started)
    try:
        size = archive.create(name)
    except BackupError as error:
        outcome = BackupOutcome("failed", None, None, error.code)
    else:
        outcome = _store(archive, name, size, started)
    with uow_factory() as uow:
        uow.backups.finish(backup_id, outcome, clock())
        uow.commit()
    return outcome


def _store(archive: BackupArchive, name: str, size: int, now: datetime) -> BackupOutcome:
    failure: str | None = None
    try:
        for old in expired(archive.names(), now):
            archive.remove(old)
    except BackupError:
        # Новая копия есть, но старые копятся: это сбой, о нём должен узнать администратор.
        failure = RETENTION_FAILED
    try:
        archive.upload()
    except BackupError as error:
        failure = failure or error.code
    if failure is not None:
        return BackupOutcome("failed", name, size, failure)
    return BackupOutcome("ok", name, size, None)
