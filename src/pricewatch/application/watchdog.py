"""Сторож на сервере (DEVELOPMENT_ARCHITECTURE.md, разделы 1 и 10): обход планировщика раз в 30 секунд.

- Worker молчит дольше 5 минут — администраторам в колокольчик и в Telegram; бот молчит дольше 5 минут —
  только в колокольчик (Telegram идёт через бота).
- На каждое молчание — одно сообщение; когда процесс снова выходит на связь — сообщение о восстановлении
  в те же каналы. Новое молчание того же процесса — новый инцидент, а не повтор старого.
- Отметка молчания и уведомления — в одной транзакции: два сторожа одновременно не сообщат дважды.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.application.ports import Silence, UnitOfWork

SILENCE_LIMIT = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class WatchReport:
    silent: tuple[str, ...] = ()
    recovered: tuple[str, ...] = ()


def watch(uow: UnitOfWork, now: datetime) -> WatchReport:
    seen_before = now - SILENCE_LIMIT
    silent: list[str] = []
    recovered: list[str] = []
    with uow:
        for process, repository, telegram in (("worker", uow.workers, True), ("bot", uow.services, False)):
            for silence in repository.report_silent(seen_before):
                _notify(uow, f"{process}_silent", silence, now, telegram)
                silent.append(f"{process}:{silence.name}")
            for silence in repository.report_recovered():
                _notify(uow, f"{process}_back", silence, now, telegram)
                recovered.append(f"{process}:{silence.name}")
        uow.commit()
    return WatchReport(tuple(silent), tuple(recovered))


def _notify(uow: UnitOfWork, kind: str, silence: Silence, now: datetime, telegram: bool) -> None:
    # Ключ — процесс и начало молчания: повтор обхода не создаёт строк, новое молчание — новое уведомление.
    since = silence.silent_since.isoformat()
    payload: dict[str, object] = {"name": silence.name, "since": since, "last_seen": silence.last_seen_at.isoformat()}
    uow.notifications.notify_admins(kind, f"{kind}:{silence.process_id}:{since}", payload, now, telegram=telegram)
