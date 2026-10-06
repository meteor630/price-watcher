"""Расписание: времена из настройки, ключ слота, наступивший слот и пропущенный запуск."""

from datetime import UTC, date, datetime, time

import pytest

from pricewatch.domain.days import MOSCOW, day_start
from pricewatch.domain.schedule import DEFAULT_TIMES, due_slot, parse_times, slot_key


def msk(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=MOSCOW)


def test_default_is_nine_moscow() -> None:
    assert DEFAULT_TIMES == (time(9, 0),)


def test_times_are_sorted() -> None:
    assert parse_times(["21:30", "09:00", "14:05"]) == (time(9, 0), time(14, 5), time(21, 30))


@pytest.mark.parametrize(
    "raw",
    [[], ["09:00", "12:00", "15:00", "18:00"], ["9:00"], ["24:00"], ["09:60"], ["09:00", "09:00"], [900], "09:00", None, ["09:00 "]],
)
def test_bad_times_are_refused(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_times(raw)


def test_slot_key() -> None:
    assert slot_key(date(2026, 10, 5), time(9, 0)) == "2026-10-05T09:00"


def test_slot_is_due_exactly_at_its_time() -> None:
    assert due_slot(DEFAULT_TIMES, msk(5, 8, 59, 30), msk(5, 9)) == "2026-10-05T09:00"
    assert due_slot(DEFAULT_TIMES, msk(5, 8, 59), msk(5, 8, 59, 59)) is None
    assert due_slot(DEFAULT_TIMES, msk(5, 9), msk(5, 9, 0, 30)) is None  # граница «после» не включается


def test_moscow_time_not_utc() -> None:
    nine_moscow = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
    assert due_slot(DEFAULT_TIMES, datetime(2026, 10, 5, 5, 59, tzinfo=UTC), nine_moscow) == "2026-10-05T09:00"


def test_missed_run_after_start_is_only_the_latest_of_today() -> None:
    times = parse_times(["09:00", "14:00", "21:00"])
    now = msk(5, 15)

    assert due_slot(times, day_start(now.date()), now) == "2026-10-05T14:00"
    assert due_slot(times, day_start(now.date()), msk(5, 8)) is None  # сегодня ещё ничего не наступило; вчерашние не догоняются


def test_tick_over_midnight() -> None:
    times = parse_times(["00:00"])

    assert due_slot(times, msk(5, 23, 59, 50), msk(6, 0, 0, 10)) == "2026-10-06T00:00"


def test_naive_time_refused() -> None:
    with pytest.raises(ValueError):
        due_slot(DEFAULT_TIMES, datetime(2026, 10, 5, 8), msk(5, 9))
