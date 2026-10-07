"""pricewatch.domain.totals: итог, изменение «только из-за цен», минимум периода, привязки ссылок."""

from datetime import UTC, datetime, timedelta

import pytest

from pricewatch.domain.days import Period
from pricewatch.domain.errors import PriceWatcherError
from pricewatch.domain.money import Direction
from pricewatch.domain.pricing import STALE_AFTER, Availability, Confirmation, LinkInput, Observation, PriceStatus
from pricewatch.domain.shopping import ItemState
from pricewatch.domain.totals import (
    Binding,
    PeriodMinimum,
    TotalItem,
    counted_unit_price,
    in_total_shown,
    item_price_at,
    period_minimum,
    total,
    total_change,
)

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)  # 12:00 по Москве
TODAY = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)  # 00:00 по Москве
LONG_AGO = datetime(2024, 1, 1, tzinfo=UTC)


def seen(at: datetime, price: int | None, *, card: int | None = None, confirmation: Confirmation = Confirmation.CONFIRMED) -> Observation:
    availability = Availability.IN_STOCK if price is not None else Availability.OUT_OF_STOCK
    return Observation(at, None, price, card, availability, confirmation)


def bound(*observations: Observation, link_id: int = 1, store_id: int = 1, enabled: bool = True,
          since: datetime = LONG_AGO, until: datetime | None = None) -> Binding:
    return Binding(LinkInput(link_id, store_id, enabled, observations), since, until)


def item(*bindings: Binding, item_id: int = 1, quantity: int = 1, in_total: bool = True, include_stale: bool = False,
         state: ItemState = ItemState.ACTIVE, stale_after: timedelta = STALE_AFTER) -> TotalItem:
    return TotalItem(item_id, state, quantity, in_total, include_stale, bindings, stale_after)


# --- итог ---


def test_total_is_best_price_times_quantity() -> None:
    items = [
        item(bound(seen(NOW - timedelta(hours=1), 100_000), store_id=1), bound(seen(NOW - timedelta(hours=2), 90_000), link_id=2, store_id=2), quantity=2),
        item(bound(seen(NOW - timedelta(hours=1), 50_050, card=45_000)), item_id=2, quantity=3),
    ]

    assert total(items, NOW) == 90_000 * 2 + 45_000 * 3


def test_quantity_changes_total_but_not_unit_price() -> None:
    one = item(bound(seen(NOW - timedelta(hours=1), 100_000)), quantity=1)
    five = item(bound(seen(NOW - timedelta(hours=1), 100_000)), quantity=5)

    assert total([five], NOW) == 5 * total([one], NOW)
    assert item_price_at(five, NOW).price_minor == item_price_at(one, NOW).price_minor == 100_000


def test_watching_only_items_are_not_in_total() -> None:
    assert total([item(bound(seen(NOW - timedelta(hours=1), 100_000)), in_total=False)], NOW) == 0


@pytest.mark.parametrize("state", [ItemState.PURCHASED, ItemState.ARCHIVED])
def test_purchased_and_archived_are_not_in_total(state: ItemState) -> None:
    assert total([item(bound(seen(NOW - timedelta(hours=1), 100_000)), state=state)], NOW) == 0


def test_items_without_price_add_nothing() -> None:
    items = [
        item(bound(seen(NOW - timedelta(hours=1), None))),  # нет в наличии
        item(bound(), item_id=2),  # ожидает проверки
        item(item_id=3),  # нет ссылок
        item(bound(seen(NOW - timedelta(hours=1), 100_000), enabled=False), item_id=4),  # магазин выключен
    ]

    assert total(items, NOW) == 0


def test_empty_list_total_is_zero() -> None:
    assert total([], NOW) == 0


# --- устаревшая цена и отметка «в итоге» ---


def stale_item(include_stale: bool, in_total: bool = True) -> TotalItem:
    return item(bound(seen(NOW - STALE_AFTER, 100_000)), include_stale=include_stale, in_total=in_total)


def test_stale_price_leaves_total_and_unchecks_box() -> None:
    stale = stale_item(include_stale=False)
    price = item_price_at(stale, NOW)

    assert price.status is PriceStatus.STALE
    assert not in_total_shown(stale, price)
    assert total([stale], NOW) == 0


def test_stale_price_counted_when_box_turned_on_again() -> None:
    stale = stale_item(include_stale=True)

    assert in_total_shown(stale, item_price_at(stale, NOW))
    assert total([stale], NOW) == 100_000


def test_include_stale_does_not_override_watching_only() -> None:
    assert total([stale_item(include_stale=True, in_total=False)], NOW) == 0


def test_include_stale_does_not_count_other_statuses() -> None:
    out_of_stock = item(bound(seen(NOW - timedelta(hours=1), None)), include_stale=True)

    assert counted_unit_price(out_of_stock, item_price_at(out_of_stock, NOW)) is None


def test_fresh_price_box_follows_in_total_only() -> None:
    fresh = item(bound(seen(NOW - timedelta(hours=1), 100_000)), include_stale=True)

    assert in_total_shown(fresh, item_price_at(fresh, NOW))


# --- изменение за период ---


def test_daily_change_against_last_price_before_midnight() -> None:
    lamp = item(bound(
        seen(TODAY - timedelta(hours=5), 130_000),
        seen(TODAY - timedelta(microseconds=1), 129_900),  # 23:59:59.999999 — ещё вчера
        seen(TODAY, 120_000),  # ровно 00:00 — уже сегодня
        seen(NOW - timedelta(hours=1), 123_380),
    ))

    change = total_change([lamp], Period.DAY, NOW)

    assert change is not None
    assert (change.direction, change.change_minor) == (Direction.DOWN, 123_380 - 129_900)


def test_change_counts_quantity() -> None:
    pair = item(bound(seen(TODAY - timedelta(hours=1), 100_000), seen(NOW - timedelta(hours=1), 110_000)), quantity=2)

    change = total_change([pair], Period.DAY, NOW)

    assert change is not None
    assert (change.direction, change.change_minor, change.percent_text) == (Direction.UP, 20_000, "10,0%")


def test_new_item_is_not_a_price_rise() -> None:
    old = item(bound(seen(TODAY - timedelta(hours=1), 100_000), seen(NOW - timedelta(hours=1), 100_000)))
    new = item(bound(seen(NOW - timedelta(hours=1), 500_000)), item_id=2)

    change = total_change([old, new], Period.DAY, NOW)

    assert change is not None
    assert (change.direction, change.change_minor, change.percent_text) == (Direction.FLAT, 0, "0,0%")


def test_no_earlier_price_means_no_change() -> None:
    assert total_change([item(bound(seen(NOW - timedelta(hours=1), 100_000)))], Period.DAY, NOW) is None
    assert total_change([], Period.WEEK, NOW) is None


def test_item_that_lost_price_is_left_out_of_change() -> None:
    gone = item(bound(seen(TODAY - timedelta(hours=1), 100_000), seen(NOW - timedelta(hours=1), None)))
    steady = item(bound(seen(TODAY - timedelta(hours=1), 50_000), seen(NOW - timedelta(hours=1), 55_000)), item_id=2)

    change = total_change([gone, steady], Period.DAY, NOW)

    assert change is not None and change.change_minor == 5_000


@pytest.mark.parametrize(("period", "days"), [(Period.WEEK, 7), (Period.MONTH, 30), (Period.YEAR, 365)])
def test_longer_periods_compare_with_midnight_n_days_ago(period: Period, days: int) -> None:
    start = TODAY - timedelta(days=days)
    tv = item(bound(seen(start - timedelta(hours=1), 200_000), seen(start + timedelta(hours=1), 1), seen(NOW - timedelta(hours=1), 150_000)),
              stale_after=timedelta(days=400))

    change = total_change([tv], period, NOW)

    assert change is not None and change.change_minor == -50_000


def test_change_skips_price_stale_at_period_start() -> None:
    # До 00:00 цена была старше 7 суток — тогда она в итог не шла, сравнивать не с чем.
    tv = item(bound(seen(TODAY - STALE_AFTER - timedelta(hours=1), 100_000), seen(NOW - timedelta(hours=1), 90_000)))

    assert total_change([tv], Period.DAY, NOW) is None


def test_change_ignores_watching_only() -> None:
    watched = item(bound(seen(TODAY - timedelta(hours=1), 100_000), seen(NOW - timedelta(hours=1), 10_000)), in_total=False)

    assert total_change([watched], Period.DAY, NOW) is None


# --- привязки ссылок и непрерывная история ---


def test_replaced_link_continues_history() -> None:
    switch = TODAY + timedelta(hours=2)
    lamp = item(
        bound(seen(TODAY - timedelta(hours=3), 100_000), link_id=1, until=switch),
        bound(seen(switch + timedelta(minutes=5), 95_000), link_id=2, since=switch),
    )

    assert item_price_at(lamp, TODAY - timedelta(hours=1)).price_minor == 100_000
    assert item_price_at(lamp, NOW).price_minor == 95_000
    change = total_change([lamp], Period.DAY, NOW)
    assert change is not None and change.change_minor == -5_000


def test_binding_bounds_are_half_open() -> None:
    start, end = TODAY, TODAY + timedelta(hours=1)
    binding = bound(since=start, until=end)

    assert binding.active_at(start)
    assert not binding.active_at(start - timedelta(microseconds=1))
    assert not binding.active_at(end)


def test_all_links_closed_is_no_links_and_out_of_total() -> None:
    lamp = item(bound(seen(TODAY - timedelta(hours=3), 100_000), until=TODAY))

    assert item_price_at(lamp, NOW).status is PriceStatus.NO_LINKS
    assert total([lamp], NOW) == 0


def test_newly_bound_link_uses_its_existing_observations() -> None:
    # Ссылка общая: её уже проверяли для другого пользователя — цена видна сразу.
    lamp = item(bound(seen(NOW - timedelta(hours=3), 100_000), since=NOW - timedelta(minutes=1)))

    assert item_price_at(lamp, NOW).price_minor == 100_000


# --- минимум периода ---


def test_period_minimum_lowest_confirmed_unit_price() -> None:
    lamp = item(
        bound(seen(TODAY + timedelta(hours=1), 100_000), seen(TODAY + timedelta(hours=3), 90_000, card=85_000), store_id=1),
        bound(seen(TODAY + timedelta(hours=2), 88_000), link_id=2, store_id=2),
        quantity=4,
    )

    assert period_minimum(lamp, Period.DAY, NOW) == PeriodMinimum(85_000, TODAY + timedelta(hours=3), 1)


def test_period_minimum_ignores_unconfirmed_out_of_stock_disabled_and_outside() -> None:
    lamp = item(
        bound(
            seen(TODAY - timedelta(microseconds=1), 10_000),  # вчера
            seen(TODAY + timedelta(hours=1), 20_000, confirmation=Confirmation.PENDING),
            seen(TODAY + timedelta(hours=2), 30_000, confirmation=Confirmation.REJECTED),
            seen(TODAY + timedelta(hours=3), None),
            seen(TODAY, 100_000),  # ровно 00:00 — входит
        ),
        bound(seen(TODAY + timedelta(hours=1), 40_000), link_id=2, store_id=2, enabled=False),
    )

    assert period_minimum(lamp, Period.DAY, NOW) == PeriodMinimum(100_000, TODAY, 1)


def test_period_minimum_only_while_link_was_bound() -> None:
    lamp = item(bound(seen(TODAY + timedelta(hours=1), 50_000), seen(TODAY + timedelta(hours=3), 70_000), since=TODAY + timedelta(hours=2)))

    assert period_minimum(lamp, Period.DAY, NOW) == PeriodMinimum(70_000, TODAY + timedelta(hours=3), 1)


def test_period_minimum_tie_takes_earlier() -> None:
    lamp = item(bound(seen(TODAY + timedelta(hours=3), 50_000), seen(TODAY + timedelta(hours=1), 50_000)))

    minimum = period_minimum(lamp, Period.DAY, NOW)
    assert minimum is not None and minimum.checked_at == TODAY + timedelta(hours=1)


def test_period_minimum_none_without_confirmed_prices() -> None:
    assert period_minimum(item(bound(seen(TODAY - timedelta(days=2), 1_000))), Period.DAY, NOW) is None
    assert period_minimum(item(bound(seen(TODAY - timedelta(days=2), 1_000))), Period.WEEK, NOW) is not None


# --- отказ ---


def test_binding_must_end_after_start() -> None:
    with pytest.raises(ValueError):
        bound(since=TODAY, until=TODAY)


def test_binding_needs_timezone() -> None:
    with pytest.raises(ValueError):
        bound(since=datetime(2026, 10, 6))


@pytest.mark.parametrize("quantity", [0, 1000, True])
def test_quantity_validated(quantity: int) -> None:
    with pytest.raises(PriceWatcherError):
        item(quantity=quantity)


def test_price_exactly_seven_days_before_midnight_still_counts_for_day_change() -> None:
    # Сразу перед 00:00 этой цене 7 суток без микросекунды — она ещё действовала.
    tv = item(bound(seen(TODAY - STALE_AFTER, 100_000), seen(NOW - timedelta(hours=1), 90_000)))

    change = total_change([tv], Period.DAY, NOW)

    assert change is not None and change.change_minor == -10_000


def test_stale_after_comes_from_the_item() -> None:
    # Срок актуальности задаёт администратор: та же цена 3 суток назад актуальна при 7 сутках и устарела при 3.
    observed = bound(seen(NOW - timedelta(days=3), 100_000))

    assert total([item(observed)], NOW) == 100_000
    assert total([item(observed, stale_after=timedelta(days=3))], NOW) == 0
    assert item_price_at(item(observed, stale_after=timedelta(days=3)), NOW).status is PriceStatus.STALE
