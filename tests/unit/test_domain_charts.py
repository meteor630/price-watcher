"""pricewatch.domain.charts: точки по московским дням, график магазина и график суммы."""

from datetime import UTC, date, datetime, timedelta

import pytest

from pricewatch.domain.charts import ChartPoint, ChartRange, PointStatus, chart_days, store_chart, sum_chart
from pricewatch.domain.pricing import STALE_AFTER, Availability, Confirmation, LinkInput, Observation
from pricewatch.domain.shopping import ItemState
from pricewatch.domain.totals import Binding, TotalItem

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)  # 12:00 по Москве, 6 октября
TODAY = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)  # 00:00 по Москве, 6 октября
LONG_AGO = datetime(2024, 1, 1, tzinfo=UTC)
NBSP = " "


def day_at(days_ago: int, hour: int = 12) -> datetime:
    """Момент по Москве: `days_ago` суток назад, в `hour` часов."""
    return TODAY - timedelta(days=days_ago) + timedelta(hours=hour)


def seen(at: datetime, price: int | None, confirmation: Confirmation = Confirmation.CONFIRMED) -> Observation:
    availability = Availability.IN_STOCK if price is not None else Availability.OUT_OF_STOCK
    return Observation(at, None, price, None, availability, confirmation)


def bound(*observations: Observation, link_id: int = 1, store_id: int = 1, enabled: bool = True,
          since: datetime = LONG_AGO, until: datetime | None = None) -> Binding:
    return Binding(LinkInput(link_id, store_id, enabled, observations), since, until)


def item(*bindings: Binding, item_id: int = 1, quantity: int = 1, in_total: bool = True, include_stale: bool = False,
         state: ItemState = ItemState.ACTIVE) -> TotalItem:
    return TotalItem(item_id, state, quantity, in_total, include_stale, bindings)


# --- дни ---


@pytest.mark.parametrize(("chart_range", "count"), [(ChartRange.WEEK, 7), (ChartRange.MONTH, 30), (ChartRange.YEAR, 365)])
def test_point_count_and_last_day_is_today(chart_range: ChartRange, count: int) -> None:
    days = chart_days(chart_range, NOW)

    assert len(days) == count
    assert days[-1] == (date(2026, 10, 6), NOW)
    assert [day for day, _ in days] == sorted(day for day, _ in days)


def test_past_day_is_read_at_last_moment_before_midnight() -> None:
    (day, moment), _ = chart_days(ChartRange.WEEK, NOW)[-2:]

    assert day == date(2026, 10, 5)
    assert moment == TODAY - timedelta(microseconds=1)


def test_today_follows_moscow_midnight() -> None:
    just_before = datetime(2026, 10, 5, 20, 59, 59, tzinfo=UTC)

    assert chart_days(ChartRange.WEEK, just_before)[-1][0] == date(2026, 10, 5)
    assert chart_days(ChartRange.WEEK, TODAY)[-1][0] == date(2026, 10, 6)


def test_year_spans_leap_day_with_365_points() -> None:
    now = datetime(2028, 3, 1, 9, 0, tzinfo=UTC)
    days = [day for day, _ in chart_days(ChartRange.YEAR, now)]

    assert (days[0], days[-1], len(days)) == (date(2027, 3, 3), date(2028, 3, 1), 365)
    assert date(2028, 2, 29) in days


# --- график магазина ---


def test_store_chart_takes_last_price_of_each_day() -> None:
    lamp = item(bound(seen(day_at(2, 9), 100_000), seen(day_at(2, 18), 95_000), seen(day_at(0, 10), 90_000)), quantity=3)

    points = store_chart(lamp, 1, ChartRange.WEEK, NOW)

    assert [p.value_minor for p in points[-3:]] == [95_000, 95_000, 90_000]  # количество не влияет
    assert points[-1] == ChartPoint(date(2026, 10, 6), 90_000, f"6 окт · 900{NBSP}₽", PointStatus.OK)


def test_store_chart_days_before_first_check_have_no_data() -> None:
    points = store_chart(item(bound(seen(day_at(1), 100_000))), 1, ChartRange.WEEK, NOW)

    assert [p.status for p in points] == [PointStatus.NO_DATA] * 5 + [PointStatus.OK] * 2
    assert points[0].label == "30 сен · нет данных"


def test_store_chart_stale_days_have_no_value() -> None:
    points = store_chart(item(bound(seen(day_at(9), 100_000))), 1, ChartRange.WEEK, NOW)

    assert points[-1] == ChartPoint(date(2026, 10, 6), None, "6 окт · цена не актуальна", PointStatus.STALE)


def test_store_chart_out_of_stock_day() -> None:
    points = store_chart(item(bound(seen(day_at(2), 100_000), seen(day_at(1), None))), 1, ChartRange.WEEK, NOW)

    assert (points[-1].status, points[-1].value_minor, points[-1].label) == (PointStatus.NO_DATA, None, "6 окт · нет в наличии")


def test_store_chart_ignores_unconfirmed_prices() -> None:
    points = store_chart(item(bound(seen(day_at(2), 100_000), seen(day_at(1), 1_000, Confirmation.PENDING))), 1, ChartRange.WEEK, NOW)

    assert points[-1].value_minor == 100_000


def test_replaced_link_of_same_store_continues_one_line() -> None:
    switch = day_at(2, 15)
    lamp = item(
        bound(seen(day_at(4), 100_000), link_id=1, store_id=3, until=switch),
        bound(seen(day_at(2, 16), 98_000), seen(day_at(1), 97_000), link_id=2, store_id=3, since=switch),
    )

    points = store_chart(lamp, 3, ChartRange.WEEK, NOW)

    assert [p.value_minor for p in points[-5:]] == [100_000, 100_000, 98_000, 97_000, 97_000]


def test_closed_link_stops_store_line_but_keeps_its_past() -> None:
    lamp = item(bound(seen(day_at(4), 100_000), until=day_at(2, 0)))

    points = store_chart(lamp, 1, ChartRange.WEEK, NOW)

    assert [p.value_minor for p in points[-5:]] == [100_000, 100_000, None, None, None]


def test_disabled_store_keeps_its_own_history() -> None:
    points = store_chart(item(bound(seen(day_at(1), 100_000), enabled=False)), 1, ChartRange.WEEK, NOW)

    assert points[-1].value_minor == 100_000


def test_store_chart_empty_without_any_price() -> None:
    assert store_chart(item(bound(seen(day_at(1), None))), 1, ChartRange.WEEK, NOW) == []
    assert store_chart(item(bound(seen(day_at(1), 100_000))), 2, ChartRange.WEEK, NOW) == []


# --- график суммы ---


def test_sum_chart_uses_todays_quantities_for_past_days() -> None:
    lamp = item(bound(seen(day_at(3), 100_000), seen(day_at(1), 80_000)), quantity=2)
    chair = item(bound(seen(day_at(2), 50_000)), item_id=2, quantity=1)

    points = sum_chart([lamp, chair], ChartRange.WEEK, NOW)

    assert [p.value_minor for p in points[-4:]] == [200_000, 250_000, 210_000, 210_000]
    assert points[-1].label == f"6 окт · 2 100{NBSP}₽"


def test_sum_chart_skips_watching_purchased_and_disabled_stores() -> None:
    lamp = item(bound(seen(day_at(1), 100_000)))
    watched = item(bound(seen(day_at(1), 7_000)), item_id=2, in_total=False)
    bought = item(bound(seen(day_at(1), 9_000)), item_id=3, state=ItemState.PURCHASED)
    disabled = item(bound(seen(day_at(1), 5_000), enabled=False), item_id=4)

    assert sum_chart([lamp, watched, bought, disabled], ChartRange.WEEK, NOW)[-1].value_minor == 100_000


def test_sum_chart_stale_only_day_has_no_value() -> None:
    stale = item(bound(seen(day_at(9), 100_000)))

    points = sum_chart([stale], ChartRange.WEEK, NOW)

    assert points[-1] == ChartPoint(date(2026, 10, 6), None, "6 окт · цена не актуальна", PointStatus.STALE)
    assert points[0].value_minor == 100_000  # 30 сентября цене было меньше 7 суток


def test_sum_chart_counts_stale_when_box_turned_on() -> None:
    stale = item(bound(seen(day_at(9), 100_000)), include_stale=True)

    assert sum_chart([stale], ChartRange.WEEK, NOW)[-1].value_minor == 100_000


def test_sum_chart_continues_across_link_replacement() -> None:
    switch = day_at(2, 15)
    lamp = item(
        bound(seen(day_at(4), 100_000), link_id=1, store_id=1, until=switch),
        bound(seen(day_at(2, 16), 90_000), link_id=2, store_id=2, since=switch),
    )

    assert [p.value_minor for p in sum_chart([lamp], ChartRange.WEEK, NOW)[-5:]] == [100_000, 100_000, 90_000, 90_000, 90_000]


def test_sum_chart_empty_for_empty_list_or_no_history() -> None:
    assert sum_chart([], ChartRange.MONTH, NOW) == []
    assert sum_chart([item(bound())], ChartRange.MONTH, NOW) == []


def test_range_values_match_api_contract() -> None:
    assert [r.value for r in ChartRange] == ["week", "month", "year"]
    assert {s.value for s in PointStatus} == {"ok", "no_data", "stale"}


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError):
        chart_days(ChartRange.WEEK, datetime(2026, 10, 6, 12, 0))


def test_stale_boundary_on_chart_matches_seven_days() -> None:
    # Цена снята 29 сентября в 12:00: на конец 5 октября ей 6,5 суток, 6 октября в 12:00 — ровно 7.
    points = store_chart(item(bound(seen(day_at(7), 100_000))), 1, ChartRange.WEEK, NOW, stale_after=STALE_AFTER)

    assert [p.status for p in points[-2:]] == [PointStatus.OK, PointStatus.STALE]
    assert points[0].status is PointStatus.OK
