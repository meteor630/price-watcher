"""Цикл планировщика с подменёнными сценариями: пропущенный запуск, слот один раз, пропуск при идущем запуске, сбой обхода."""

import logging
from datetime import datetime, time, timedelta

import pytest

from pricewatch.application.ports import PurgedCounts, RunProgress
from pricewatch.application.runs import ExpiredLeases, ScheduledStart
from pricewatch.application.watchdog import WatchReport
from pricewatch.domain.days import MOSCOW
from pricewatch.scheduler.loop import Scheduler

RUN = RunProgress(7, "scheduled", "queued", 3, 0, 0)


class FakeJobs:
    def __init__(self, times: tuple[time, ...] = (time(9, 0),)) -> None:
        self.times = times
        self.started: list[str] = []
        self.outcome = ScheduledStart(progress=RUN)
        self.expired = ExpiredLeases()
        self.expire_calls = 0
        self.deleted = 0
        self.deleted_at: list[datetime] = []
        self.purged = PurgedCounts()
        self.purged_at: list[datetime] = []
        self.keys_purged_at: list[datetime] = []
        self.reports_purged_at: list[datetime] = []
        self.watched = WatchReport()
        self.watched_at: list[datetime] = []
        self.fail: Exception | None = None

    def schedule_times(self) -> tuple[time, ...]:
        if self.fail is not None:
            raise self.fail
        return self.times

    def start_scheduled_run(self, slot_key: str, now: datetime) -> ScheduledStart:
        self.started.append(slot_key)
        return self.outcome

    def expire_leases(self, now: datetime) -> ExpiredLeases:
        self.expire_calls += 1
        return self.expired

    def delete_blocked_users_data(self, now: datetime) -> int:
        self.deleted_at.append(now)
        return self.deleted

    def purge_deleted(self, now: datetime) -> PurgedCounts:
        self.purged_at.append(now)
        return self.purged

    def purge_idempotency_keys(self, now: datetime) -> int:
        self.keys_purged_at.append(now)
        return 0

    def purge_client_errors(self, now: datetime) -> int:
        self.reports_purged_at.append(now)
        return 0

    def watch(self, now: datetime) -> WatchReport:
        self.watched_at.append(now)
        return self.watched


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def msk(hour: int, minute: int = 0, day: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=MOSCOW)


def test_missed_slot_of_today_runs_after_start() -> None:
    jobs = FakeJobs((time(9, 0), time(14, 0)))
    Scheduler(jobs, clock=Clock(msk(15))).tick()

    assert jobs.started == ["2026-10-06T14:00"]  # один, последний


def test_nothing_due_before_first_slot() -> None:
    jobs = FakeJobs()
    Scheduler(jobs, clock=Clock(msk(8, 59))).tick()

    assert jobs.started == []
    assert jobs.expire_calls == 1  # просроченные задания проверяются на каждом обходе


def test_slot_fires_once_as_time_passes() -> None:
    jobs, clock = FakeJobs(), Clock(msk(8, 59))
    scheduler = Scheduler(jobs, clock=clock)

    for _ in range(4):
        scheduler.tick()
        clock.now += timedelta(seconds=30)

    assert jobs.started == ["2026-10-06T09:00"]


def test_skip_is_logged_when_another_run_is_going(caplog: pytest.LogCaptureFixture) -> None:
    jobs = FakeJobs()
    jobs.outcome = ScheduledStart(blocked_by=RunProgress(5, "manual", "running", 10, 4, 0))

    with caplog.at_level(logging.WARNING):
        Scheduler(jobs, clock=Clock(msk(9, 0))).tick()

    record = next(r for r in caplog.records if r.getMessage() == "Плановая проверка пропущена: идёт другая")
    assert (record.slot, record.run_id) == ("2026-10-06T09:00", 5)  # type: ignore[attr-defined]


def test_failed_tick_is_logged_and_slot_is_retried(caplog: pytest.LogCaptureFixture) -> None:
    jobs, clock = FakeJobs(), Clock(msk(9, 0))
    jobs.fail = ConnectionError("база недоступна")
    waits: list[float] = []
    running: list[Scheduler] = []

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        if len(waits) == 1:
            jobs.fail = None  # база вернулась
            clock.now += timedelta(seconds=30)
        else:
            running[0].stop()
        return False

    scheduler = Scheduler(jobs, clock=clock, wait=wait)
    running.append(scheduler)
    with caplog.at_level(logging.ERROR):
        scheduler.run()

    assert any(r.getMessage() == "Сбой обхода планировщика" and r.exc_info for r in caplog.records)
    assert jobs.started == ["2026-10-06T09:00"]
    assert waits == [30.0, 30.0]


def test_expired_leases_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    jobs = FakeJobs()
    jobs.expired = ExpiredLeases(requeued=2, failed=1)

    with caplog.at_level(logging.WARNING):
        Scheduler(jobs, clock=Clock(msk(8))).tick()

    record = next(r for r in caplog.records if r.getMessage() == "Аренда заданий истекла")
    assert (record.requeued, record.failed, record.cancelled) == (2, 1, 0)  # type: ignore[attr-defined]


def test_stop_ends_loop() -> None:
    jobs = FakeJobs()
    scheduler = Scheduler(jobs, clock=Clock(msk(8)), wait=lambda seconds: False)
    jobs.expire_leases = lambda now: scheduler.stop() or ExpiredLeases()  # type: ignore[method-assign]

    scheduler.run()

    assert jobs.started == []


def test_blocked_users_data_is_checked_every_tick_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    jobs, clock = FakeJobs(), Clock(msk(8))
    jobs.deleted = 2
    scheduler = Scheduler(jobs, clock=clock)

    with caplog.at_level(logging.INFO):
        scheduler.tick()
        jobs.deleted = 0
        clock.now += timedelta(seconds=30)
        scheduler.tick()

    assert jobs.deleted_at == [msk(8), msk(8) + timedelta(seconds=30)]
    records = [r for r in caplog.records if r.getMessage() == "Данные заблокированных пользователей удалены"]
    assert [r.users for r in records] == [2]  # type: ignore[attr-defined]


def test_purge_runs_every_tick_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    jobs = FakeJobs()
    jobs.purged = PurgedCounts(categories=1, groups=0, items=4)

    with caplog.at_level(logging.INFO):
        Scheduler(jobs, clock=Clock(msk(8))).tick()

    assert jobs.purged_at == [msk(8)]
    record = next(r for r in caplog.records if r.getMessage() == "Удалённое стёрто окончательно")
    assert (record.categories, record.groups, record.items) == (1, 0, 4)  # type: ignore[attr-defined]


def test_old_idempotency_keys_are_purged_every_tick() -> None:
    jobs = FakeJobs()

    Scheduler(jobs, clock=Clock(msk(8))).tick()

    assert jobs.keys_purged_at == [msk(8)]
    assert jobs.reports_purged_at == [msk(8)]


def test_watchdog_runs_every_tick_and_silence_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    jobs, clock = FakeJobs(), Clock(msk(8))
    scheduler = Scheduler(jobs, clock=clock)
    jobs.watched = WatchReport(silent=("worker:home-pi",), recovered=("bot:telegram",))

    with caplog.at_level(logging.INFO):
        scheduler.tick()
    jobs.watched = WatchReport()
    clock.now += timedelta(seconds=30)
    scheduler.tick()

    assert jobs.watched_at == [msk(8), msk(8) + timedelta(seconds=30)]
    silent = next(r for r in caplog.records if r.getMessage() == "Процессы молчат дольше 5 минут")
    back = next(r for r in caplog.records if r.getMessage() == "Процессы снова на связи")
    assert silent.levelno == logging.WARNING and silent.processes == ["worker:home-pi"]  # type: ignore[attr-defined]
    assert back.processes == ["bot:telegram"]  # type: ignore[attr-defined]
