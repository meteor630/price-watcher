"""Цикл планировщика: каждые 30 секунд вернуть просроченные задания, удалить данные пользователей,
заблокированных 7 суток назад, стереть удалённое 30 дней назад, ключи повтора старше суток и отчёты об ошибках
приложения старше 90 суток, запустить наступившую проверку.

- Наступивший слот — последний из промежутка с прошлого удачного обхода; после старта промежуток
  начинается с 00:00 текущих суток по Москве: один пропущенный запуск за сутки выполняется.
- Идёт другой запуск — плановый не создаётся, пропуск пишется в журнал.
- Сбой обхода (база недоступна, неверная настройка времени) пишется в журнал с трассировкой;
  промежуток не сдвигается, слот будет запущен на следующем удачном обходе.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Protocol

from pricewatch.application.ports import PurgedCounts
from pricewatch.application.runs import ExpiredLeases, ScheduledStart
from pricewatch.domain.days import day_start, moscow_date
from pricewatch.domain.schedule import due_slot

logger = logging.getLogger(__name__)

TICK_S = 30.0


class Jobs(Protocol):
    def schedule_times(self) -> tuple[time, ...]: ...
    def start_scheduled_run(self, slot_key: str, now: datetime) -> ScheduledStart: ...
    def expire_leases(self, now: datetime) -> ExpiredLeases: ...
    def delete_blocked_users_data(self, now: datetime) -> int: ...
    def purge_deleted(self, now: datetime) -> PurgedCounts: ...
    def purge_idempotency_keys(self, now: datetime) -> int: ...
    def purge_client_errors(self, now: datetime) -> int: ...


class Scheduler:
    def __init__(
        self,
        jobs: Jobs,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        wait: Callable[[float], bool] | None = None,
        tick_s: float = TICK_S,
    ) -> None:
        self._jobs = jobs
        self._clock = clock
        self._stopping = threading.Event()
        self._wait = wait or self._stopping.wait
        self._tick_s = tick_s
        self._after: datetime | None = None

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.tick()
            except Exception:
                # Планировщик живёт дальше: следующий обход повторит то, что не удалось.
                logger.exception("Сбой обхода планировщика")
            self._wait(self._tick_s)

    def tick(self) -> None:
        now = self._clock()
        expired = self._jobs.expire_leases(now)
        if expired != ExpiredLeases():
            logger.warning(
                "Аренда заданий истекла",
                extra={"requeued": expired.requeued, "failed": expired.failed, "cancelled": expired.cancelled},
            )
        deleted = self._jobs.delete_blocked_users_data(now)
        if deleted:
            logger.info("Данные заблокированных пользователей удалены", extra={"users": deleted})
        purged = self._jobs.purge_deleted(now)
        if purged != PurgedCounts():
            logger.info(
                "Удалённое стёрто окончательно", extra={"categories": purged.categories, "groups": purged.groups, "items": purged.items}
            )
        self._jobs.purge_idempotency_keys(now)
        reports = self._jobs.purge_client_errors(now)
        if reports:
            logger.info("Отчёты об ошибках приложения старше 90 суток стёрты", extra={"reports": reports})
        after = self._after or day_start(moscow_date(now))
        slot = due_slot(self._jobs.schedule_times(), after, now)
        if slot is not None:
            self._start(slot, now)
        self._after = now

    def _start(self, slot: str, now: datetime) -> None:
        started = self._jobs.start_scheduled_run(slot, now)
        if started.progress is not None:
            logger.info("Плановая проверка запущена", extra={"slot": slot, "run_id": started.progress.run_id, "total": started.progress.total})
        elif started.blocked_by is not None:
            logger.warning("Плановая проверка пропущена: идёт другая", extra={"slot": slot, "run_id": started.blocked_by.run_id})
        elif not started.slot_done:
            logger.warning("Плановая проверка пропущена: другая проверка только что завершилась", extra={"slot": slot})
