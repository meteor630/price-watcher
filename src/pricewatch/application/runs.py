"""Запуски проверки цен (DEVELOPMENT_ARCHITECTURE.md, раздел 8).

- Плановый или ручной запуск — один на всех: второй администратор получает RUN_IN_PROGRESS
  и текущий прогресс. Плановое время при идущем запуске — запуск не создаётся, планировщик
  записывает пропуск в журнал. Слот планового запуска уникален: два планировщика не создадут два.
- В запуск попадают ссылки товаров «в списке» и купленных меньше 14 суток назад, у включённых
  магазинов и незаблокированных пользователей. Общая ссылка проверяется одним заданием.
- Остановка: ждущие задания отменяются, выданные дорабатывают; потом запуск завершается.
- Истёкшая аренда возвращает задание в очередь; после третьей попытки задание закрывается
  с ошибкой TIMEOUT, она попадает в историю ссылки. Задание остановленного запуска не возвращается.
"""

from dataclasses import dataclass
from datetime import datetime, time

from pricewatch.application.ports import NewObservation, RunProgress, UnitOfWork
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability, Confirmation
from pricewatch.domain.purchases import TRACK_AFTER_PURCHASE
from pricewatch.domain.schedule import DEFAULT_TIMES, parse_times

ATTEMPTS_MAX = 3
CHECK_TIMES_SETTING = "check_times"


@dataclass(frozen=True, slots=True)
class ScheduledStart:
    """Итог планового слота: запуск создан (progress), слот уже отработан или идёт другой запуск (blocked_by)."""

    progress: RunProgress | None = None
    slot_done: bool = False
    blocked_by: RunProgress | None = None


@dataclass(frozen=True, slots=True)
class ExpiredLeases:
    requeued: int = 0
    failed: int = 0
    cancelled: int = 0


def start_manual_run(uow: UnitOfWork, actor_id: int, now: datetime) -> RunProgress:
    with uow:
        _require_admin(uow, actor_id)
        run_id = uow.queue.start_run("manual", None, actor_id, now)
        if run_id is None:
            raise _in_progress(uow.queue.active_run())
        progress = uow.queue.enqueue_run(run_id, now - TRACK_AFTER_PURCHASE, now)
        uow.commit()
    return progress


def start_scheduled_run(uow: UnitOfWork, slot_key: str, now: datetime) -> ScheduledStart:
    with uow:
        if uow.queue.slot_taken(slot_key):
            return ScheduledStart(slot_done=True)
        run_id = uow.queue.start_run("scheduled", slot_key, None, now)
        if run_id is None:
            # Слот мог занять второй планировщик между проверкой и вставкой.
            if uow.queue.slot_taken(slot_key):
                return ScheduledStart(slot_done=True)
            return ScheduledStart(blocked_by=uow.queue.active_run())
        progress = uow.queue.enqueue_run(run_id, now - TRACK_AFTER_PURCHASE, now)
        uow.commit()
    return ScheduledStart(progress=progress)


def stop_run(uow: UnitOfWork, actor_id: int, run_id: int, now: datetime) -> RunProgress:
    with uow:
        _require_admin(uow, actor_id)
        progress = uow.queue.stop_run(run_id, now)
        if progress is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        uow.commit()
    return progress


def expire_leases(uow: UnitOfWork, now: datetime) -> ExpiredLeases:
    requeued = failed = cancelled = 0
    with uow:
        for expired in uow.queue.expired(now):
            task = expired.task
            if expired.run_stopping:
                uow.queue.cancel(task, now)
                cancelled += 1
            elif expired.attempt < ATTEMPTS_MAX:
                uow.queue.requeue(task.task_id)
                requeued += 1
            else:
                uow.observations.add(
                    NewObservation(
                        task.tracked_link_id, task.task_id, now, ErrorCode.TIMEOUT.value, None, None,
                        Availability.UNKNOWN.value, None, task.region_code, Confirmation.CONFIRMED.value, None,
                    )
                )
                uow.queue.finish(task, True, ErrorCode.TIMEOUT.value, None, now)
                failed += 1
        uow.commit()
    return ExpiredLeases(requeued, failed, cancelled)


def schedule_times(uow: UnitOfWork) -> tuple[time, ...]:
    """Времена плановых проверок; настройку не задавали — 09:00 по Москве."""
    with uow:
        return configured_times(uow)


def configured_times(uow: UnitOfWork) -> tuple[time, ...]:
    """То же внутри уже открытой транзакции."""
    raw = uow.settings.get(CHECK_TIMES_SETTING)
    return DEFAULT_TIMES if raw is None else parse_times(raw)


def _require_admin(uow: UnitOfWork, actor_id: int) -> None:
    actor = uow.users.get(actor_id)
    if not actor.is_admin or actor.disabled:
        raise PriceWatcherError(ErrorCode.FORBIDDEN)


def _in_progress(active: RunProgress | None) -> PriceWatcherError:
    if active is None:
        # Мешавший запуск успел завершиться между вставкой и чтением: прогресса уже нет, нажать можно снова.
        return PriceWatcherError(ErrorCode.RUN_IN_PROGRESS)
    progress = {"run_id": active.run_id, "kind": active.kind, "total": active.total, "done": active.done, "failed": active.failed}
    return PriceWatcherError(ErrorCode.RUN_IN_PROGRESS, {"run": progress})
