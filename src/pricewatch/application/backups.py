"""Резервная копия на сервере (DEVELOPMENT_ARCHITECTURE.md, раздел 11).

1. Начало попытки записывается в журнал копий своей транзакцией: оборванная копия остаётся в журнале «running».
2. Место: свободно меньше, чем размер последней копии × 1,5 + 1 ГБ, — копии нет (NO_SPACE), ничего не удаляется.
3. Копия создаётся вне транзакции базы: у pg_dump свой согласованный снимок.
4. Удачная копия — только теперь удаляются копии сверх двух самых новых. Неудачная — ничего не удаляется:
   последняя удачная остаётся, пока нет новой.
5. Итог — в журнал копий; по нему планировщик узнаёт о сбое и о давности последней удачной копии.
Сервер в дом не ходит: копии забирает домашний приёмник (backup_readers.py).

Сторож копий (watch_backups) — на каждом обходе планировщика, администраторам в колокольчик и в Telegram:
- неудачная попытка — одно уведомление на попытку;
- удачной копии нет больше 36 часов (domain.backups.stale_since) — одно уведомление на эпизод: новая удачная копия
  начинает отсчёт заново, и следующее такое молчание — новое уведомление;
- домашний приёмник не получал копий больше 36 часов (от последнего получения, без него — от выдачи токена) — так же,
  одно уведомление на эпизод.
Уже отправленное не повторяется (иначе каждый обход снова делал бы его непрочитанным); обход идёт под блокировкой —
два планировщика не сообщат об одном дважды.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.ports import BackupArchive, BackupError, BackupOutcome, UnitOfWork
from pricewatch.domain.backups import backup_name, latest, space_needed, stale_since, surplus

RETENTION_FAILED = "RETENTION_FAILED"
NO_SPACE = "NO_SPACE"
FAILED_KIND = "backup_failed"
STALE_KIND = "backup_stale"
NOT_PULLED_KIND = "backup_not_pulled"


@dataclass(frozen=True, slots=True)
class BackupAlerts:
    """О чём сторож копий сообщил на этом обходе."""

    failed_backup_id: int | None = None
    stale_since: datetime | None = None
    not_pulled_since: datetime | None = None


def make_backup(uow_factory: Callable[[], UnitOfWork], archive: BackupArchive, clock: Callable[[], datetime]) -> BackupOutcome:
    started = clock()
    with uow_factory() as uow:
        backup_id = uow.backups.start(started)
        uow.commit()
    name = backup_name(started)
    try:
        _check_space(archive)
        size = archive.create(name)
    except BackupError as error:
        outcome = BackupOutcome("failed", None, None, error.code)
    else:
        outcome = _rotate(archive, name, size)
    with uow_factory() as uow:
        uow.backups.finish(backup_id, outcome, clock())
        uow.commit()
    return outcome


def _check_space(archive: BackupArchive) -> None:
    last = latest(archive.names())
    needed = space_needed(None if last is None else archive.size(last))
    if archive.free_bytes() < needed:
        raise BackupError(NO_SPACE)


def _rotate(archive: BackupArchive, name: str, size: int) -> BackupOutcome:
    try:
        for old in surplus(archive.names()):
            archive.remove(old)
    except BackupError:
        # Новая копия есть, но старые копятся: это сбой, о нём должен узнать администратор.
        return BackupOutcome("failed", name, size, RETENTION_FAILED)
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
        not_pulled = _watch_pulls(uow, now)
        uow.commit()
    return BackupAlerts(failed, stale, not_pulled)


def _watch_pulls(uow: UnitOfWork, now: datetime) -> datetime | None:
    pulls = uow.backup_readers.pull_health()
    if pulls is None:
        return None
    since = stale_since(pulls.last_pulled_at, pulls.created_at, now)
    if since is None:
        return None
    key = f"{NOT_PULLED_KIND}:{since.isoformat()}"
    if uow.notifications.exists(key):
        return None
    uow.notifications.notify_admins(NOT_PULLED_KIND, key, {"since": since.isoformat(), "ever_pulled": pulls.last_pulled_at is not None}, now)
    return since
