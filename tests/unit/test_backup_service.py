"""backup.service: ожидание до времени копии по Москве, остановка, сбой попытки не останавливает службу."""

import logging
from datetime import UTC, datetime, time, timedelta

import pytest

from pricewatch.application.ports import BackupOutcome
from pricewatch.backup.service import BackupService

OK = BackupOutcome("ok", "pricewatch-2026-10-14T013000Z.tar.age", 10, None)
FAILED = BackupOutcome("failed", None, None, "DATABASE_DUMP_FAILED")


class Timeline:
    """Часы и ожидание: ожидание сдвигает часы; после заданного числа ожиданий — остановка."""

    def __init__(self, start: datetime, waits_before_stop: int) -> None:
        self.now = start
        self.waits: list[float] = []
        self.left = waits_before_stop

    def clock(self) -> datetime:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        if self.left == 0:
            return True
        self.left -= 1
        self.now += timedelta(seconds=seconds)
        return False


def test_waits_until_next_moscow_time_then_backs_up_daily() -> None:
    # 00:00 UTC = 03:00 по Москве; копия в 04:30 по Москве — через полтора часа, затем через сутки.
    timeline = Timeline(datetime(2026, 10, 14, 0, 0, tzinfo=UTC), waits_before_stop=2)
    runs: list[datetime] = []
    service = BackupService(lambda: runs.append(timeline.now) or OK, time(4, 30), clock=timeline.clock, wait=timeline.wait)
    service.run()
    assert runs == [datetime(2026, 10, 14, 1, 30, tzinfo=UTC), datetime(2026, 10, 15, 1, 30, tzinfo=UTC)]
    assert timeline.waits[:2] == [5400.0, 86400.0]


def test_exactly_at_backup_time_waits_for_next_day() -> None:
    timeline = Timeline(datetime(2026, 10, 14, 1, 30, tzinfo=UTC), waits_before_stop=0)
    BackupService(lambda: OK, time(4, 30), clock=timeline.clock, wait=timeline.wait).run()
    assert timeline.waits == [86400.0]


def test_stop_during_wait_skips_backup() -> None:
    timeline = Timeline(datetime(2026, 10, 14, 0, 0, tzinfo=UTC), waits_before_stop=0)
    runs: list[int] = []
    BackupService(lambda: runs.append(1) or OK, time(4, 30), clock=timeline.clock, wait=timeline.wait).run()
    assert runs == []


def test_crashed_attempt_is_logged_and_service_continues(caplog: pytest.LogCaptureFixture) -> None:
    timeline = Timeline(datetime(2026, 10, 14, 0, 0, tzinfo=UTC), waits_before_stop=2)
    attempts: list[int] = []

    def job() -> BackupOutcome:
        attempts.append(1)
        raise RuntimeError("база недоступна")

    with caplog.at_level(logging.ERROR):
        BackupService(job, time(4, 30), clock=timeline.clock, wait=timeline.wait).run()
    assert len(attempts) == 2
    assert [record.message for record in caplog.records if record.levelno == logging.ERROR] == ["Сбой попытки резервной копии"] * 2


@pytest.mark.parametrize(("outcome", "level"), [(OK, logging.INFO), (FAILED, logging.ERROR)])
def test_run_once_reports_outcome(outcome: BackupOutcome, level: int, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        assert BackupService(lambda: outcome, time(4, 30)).run_once() == outcome
    assert caplog.records[-1].levelno == level
