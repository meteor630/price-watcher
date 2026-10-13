"""Резервная копия на домашнем узле (DEVELOPMENT_ARCHITECTURE.md, раздел 11).

1. Начало попытки записывается в журнал копий своей транзакцией: оборванная копия остаётся в журнале «running».
2. Копия создаётся вне транзакции базы: у pg_dump свой согласованный снимок.
3. Удачная копия — домашние копии старше 30 суток удаляются, а оставшиеся, которых нет на арендном сервере, отправляются
   туда (не отправленная вчера уйдёт сегодня). Оба шага выполняются, даже если первый не удался: свежую копию важно
   отправить; итог — код первого сбоя, файл копии в журнале остаётся. Неудачная копия — ничего не удаляется и не
   отправляется: старые копии остаются, пока нет новой.
4. Итог — в журнал копий; по нему планировщик узнаёт о сбое и о давности последней удачной копии.

Сторож копий (watch_backups) — на каждом обходе планировщика, администраторам в колокольчик и в Telegram:
- неудачная попытка — одно уведомление на попытку;
- удачной копии нет больше 36 часов (domain.backups.stale_since) — одно уведомление на эпизод: новая удачная копия
  начинает отсчёт заново, и следующее такое молчание — новое уведомление.
Уже отправленное не повторяется (иначе каждый обход снова делал бы его непрочитанным); обход идёт под блокировкой —
два планировщика не сообщат об одном дважды.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.ports import BackupArchive, BackupError, BackupOutcome, UnitOfWork
from pricewatch.domain.backups import backup_name, expired, stale_since

RETENTION_FAILED = "RETENTION_FAILED"
FAILED_KIND = "backup_failed"
STALE_KIND = "backup_stale"


@dataclass(frozen=True, slots=True)
class BackupAlerts:
    """О чём сторож копий сообщил на этом обходе."""

    failed_backup_id: int | None = None
    stale_since: datetime | None = None


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


def watch_backups(uow: UnitOfWork, now: datetime) -> BackupAlerts:
    with uow:
        uow.backups.lock_watch()
        health = uow.backups.health()
        if health is None:
            return BackupAlerts()
        failed = None
        if health.latest_status == "failed" and health.latest_id is not None and health.latest_started_at is not None:
            key = f"{FAILED_KIND}:{health.latest_id}"
            if not uow.notifications.exists(key):
                payload: dict[str, object] = {"error_code": health.latest_error_code, "started_at": health.latest_started_at.isoformat()}
                uow.notifications.notify_admins(FAILED_KIND, key, payload, now)
                failed = health.latest_id
        stale = None
        since = stale_since(health.last_ok_at, health.first_started_at, now)
        if since is not None:
            key = f"{STALE_KIND}:{since.isoformat()}"
            if not uow.notifications.exists(key):
                uow.notifications.notify_admins(STALE_KIND, key, {"since": since.isoformat(), "ever_ok": health.last_ok_at is not None}, now)
                stale = since
        uow.commit()
    return BackupAlerts(failed, stale)
