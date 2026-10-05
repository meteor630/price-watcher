"""pricewatch.domain.days: московские сутки, начала периодов, подписи дат."""

from datetime import UTC, date, datetime, timedelta, timezone
from importlib.resources import files

import pytest

from pricewatch.domain.days import MOSCOW, Period, date_label, day_start, moscow_date, period_start


@pytest.mark.parametrize(
    ("moment", "day"),
    [
        (datetime(2026, 10, 5, 20, 59, 59, tzinfo=UTC), date(2026, 10, 5)),  # 23:59:59 по Москве
        (datetime(2026, 10, 5, 21, 0, 0, tzinfo=UTC), date(2026, 10, 6)),  # 00:00:00 по Москве
        (datetime(2026, 10, 5, 23, 59, 59, tzinfo=MOSCOW), date(2026, 10, 5)),
        (datetime(2026, 10, 6, 0, 0, 0, tzinfo=MOSCOW), date(2026, 10, 6)),
        (datetime(2026, 12, 31, 21, 0, tzinfo=UTC), date(2027, 1, 1)),  # новый год наступает в Москве раньше
        (datetime(2026, 10, 6, 1, 0, tzinfo=timezone(timedelta(hours=5))), date(2026, 10, 5)),
    ],
)
def test_moscow_date_boundary(moment: datetime, day: date) -> None:
    assert moscow_date(moment) == day


def test_day_start_is_moscow_midnight_in_utc() -> None:
    start = day_start(date(2026, 10, 6))

    assert start == datetime(2026, 10, 5, 21, 0, tzinfo=UTC)
    assert start.tzinfo is UTC


def test_moscow_offset_follows_history_not_fixed_plus_three() -> None:
    # 2011–2014 Москва жила по UTC+4: полночь была в 20:00 UTC.
    assert day_start(date(2013, 6, 1)) == datetime(2013, 5, 31, 20, 0, tzinfo=UTC)
    # До 2011 года летом действовало UTC+4, зимой UTC+3.
    assert day_start(date(2010, 1, 15)) == datetime(2010, 1, 14, 21, 0, tzinfo=UTC)


NOW = datetime(2026, 10, 6, 9, 30, tzinfo=UTC)  # 12:30 по Москве


@pytest.mark.parametrize(
    ("period", "start"),
    [
        (Period.DAY, datetime(2026, 10, 5, 21, 0, tzinfo=UTC)),
        (Period.WEEK, datetime(2026, 9, 28, 21, 0, tzinfo=UTC)),
        (Period.MONTH, datetime(2026, 9, 5, 21, 0, tzinfo=UTC)),
        (Period.YEAR, datetime(2025, 10, 5, 21, 0, tzinfo=UTC)),
    ],
)
def test_period_start(period: Period, start: datetime) -> None:
    assert period_start(period, NOW) == start


def test_period_start_right_before_and_at_midnight() -> None:
    before = datetime(2026, 10, 5, 20, 59, 59, tzinfo=UTC)
    at = datetime(2026, 10, 5, 21, 0, 0, tzinfo=UTC)

    assert period_start(Period.DAY, before) == datetime(2026, 10, 4, 21, 0, tzinfo=UTC)
    assert period_start(Period.DAY, at) == at


def test_year_is_365_days_even_across_leap_day() -> None:
    now = datetime(2028, 3, 1, 9, 0, tzinfo=UTC)  # 2028 — високосный

    assert moscow_date(period_start(Period.YEAR, now)) == date(2027, 3, 2)


def test_period_values_match_api_contract() -> None:
    assert [period.value for period in Period] == ["day", "week", "month", "year"]


@pytest.mark.parametrize(
    ("day", "label"),
    [(date(2026, 10, 5), "5 окт"), (date(2026, 1, 1), "1 янв"), (date(2026, 5, 9), "9 мая"), (date(2026, 12, 31), "31 дек")],
)
def test_date_label(day: date, label: str) -> None:
    assert date_label(day) == label


@pytest.mark.parametrize("call", [moscow_date, lambda moment: period_start(Period.DAY, moment)])
def test_naive_time_is_rejected(call) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        call(datetime(2026, 10, 6, 12, 0))


def test_moscow_zone_ships_with_tzdata_package() -> None:
    # На минимальном образе для Raspberry Pi системной базы часовых поясов может не быть.
    assert (files("tzdata.zoneinfo") / "Europe" / "Moscow").is_file()
