"""pricewatch.domain.purchases: переходы, данные покупки, 14 суток проверки, «дешевле», статистика."""

from datetime import UTC, datetime, timedelta

import pytest

from pricewatch.domain.days import Period
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import STALE_AFTER, Availability, Confirmation, LinkInput, Observation
from pricewatch.domain.purchases import (
    PURCHASE_PRICE_MAX,
    TRACK_AFTER_PURCHASE,
    Purchase,
    PurchaseRecord,
    PurchaseStats,
    baseline_price,
    change_state,
    cheaper_now,
    is_tracked,
    link_needs_check,
    new_purchase,
    purchase_stats,
    purchase_stats_by_category,
)
from pricewatch.domain.shopping import ItemState
from pricewatch.domain.totals import Binding, TotalItem, item_price_at

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)  # 12:00 по Москве
TODAY = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)  # 00:00 по Москве
LONG_AGO = datetime(2024, 1, 1, tzinfo=UTC)


def seen(at: datetime, price: int | None, confirmation: Confirmation = Confirmation.CONFIRMED) -> Observation:
    availability = Availability.IN_STOCK if price is not None else Availability.OUT_OF_STOCK
    return Observation(at, None, price, None, availability, confirmation)


def item(*observations: Observation, store_id: int = 1, since: datetime = LONG_AGO, more: tuple[Binding, ...] = ()) -> TotalItem:
    return TotalItem(1, ItemState.ACTIVE, 1, True, False, (Binding(LinkInput(1, store_id, True, observations), since, None), *more))


def bought(days_ago: float, price: int = 100_000, quantity: int = 1) -> Purchase:
    return Purchase(NOW - timedelta(days=days_ago), price, quantity, 1)


def reason(error: PriceWatcherError, field: str) -> str:
    assert error.code is ErrorCode.VALIDATION_ERROR
    return error.details["fields"][field]  # type: ignore[index]


# --- переходы ---


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ItemState.ACTIVE, ItemState.PURCHASED),
        (ItemState.ACTIVE, ItemState.ARCHIVED),
        (ItemState.PURCHASED, ItemState.ACTIVE),
        (ItemState.ARCHIVED, ItemState.ACTIVE),
    ],
)
def test_allowed_transitions(current: ItemState, target: ItemState) -> None:
    assert change_state(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ItemState.PURCHASED, ItemState.ARCHIVED),
        (ItemState.ARCHIVED, ItemState.PURCHASED),
        (ItemState.ACTIVE, ItemState.ACTIVE),
        (ItemState.PURCHASED, ItemState.PURCHASED),
        (ItemState.ARCHIVED, ItemState.ARCHIVED),
    ],
)
def test_other_transitions_rejected(current: ItemState, target: ItemState) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        change_state(current, target)

    assert reason(caught.value, "state") == "invalid_transition"


# --- данные покупки ---


def test_new_purchase_normal() -> None:
    assert new_purchase(129_900, 2, 3, NOW - timedelta(hours=1), NOW) == Purchase(NOW - timedelta(hours=1), 129_900, 2, 3)


def test_purchase_at_this_very_moment_and_without_store() -> None:
    assert new_purchase(1, 1, None, NOW, NOW).store_id is None


def test_purchase_price_upper_bound() -> None:
    assert new_purchase(PURCHASE_PRICE_MAX, 999, 1, NOW, NOW).price_minor == PURCHASE_PRICE_MAX


@pytest.mark.parametrize("price", [0, -1, PURCHASE_PRICE_MAX + 1, 1299.0, True])
def test_bad_purchase_price(price: int) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        new_purchase(price, 1, 1, NOW, NOW)

    assert reason(caught.value, "purchase_price") == "out_of_range"


@pytest.mark.parametrize("quantity", [0, 1000])
def test_bad_purchase_quantity(quantity: int) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        new_purchase(100, quantity, 1, NOW, NOW)

    assert reason(caught.value, "purchase_quantity") == "out_of_range"


def test_purchase_in_future_rejected() -> None:
    with pytest.raises(PriceWatcherError) as caught:
        new_purchase(100, 1, 1, NOW + timedelta(seconds=1), NOW)

    assert reason(caught.value, "purchased_at") == "in_future"


def test_purchase_needs_timezone() -> None:
    with pytest.raises(ValueError):
        new_purchase(100, 1, 1, datetime(2026, 10, 6, 12, 0), NOW)


# --- проверка ссылок после покупки ---


def test_active_item_is_tracked_and_archived_is_not() -> None:
    assert is_tracked(ItemState.ACTIVE, None, NOW)
    assert not is_tracked(ItemState.ARCHIVED, None, NOW)


def test_purchased_item_tracked_for_14_days() -> None:
    assert is_tracked(ItemState.PURCHASED, NOW - TRACK_AFTER_PURCHASE + timedelta(seconds=1), NOW)
    assert not is_tracked(ItemState.PURCHASED, NOW - TRACK_AFTER_PURCHASE, NOW)


def test_purchased_without_date_is_a_data_error() -> None:
    with pytest.raises(ValueError):
        is_tracked(ItemState.PURCHASED, None, NOW)


def test_shared_link_checked_while_anyone_tracks_it() -> None:
    old_purchase = NOW - timedelta(days=30)

    assert link_needs_check([(ItemState.ARCHIVED, None), (ItemState.PURCHASED, old_purchase), (ItemState.ACTIVE, None)], NOW)
    assert not link_needs_check([(ItemState.ARCHIVED, None), (ItemState.PURCHASED, old_purchase)], NOW)
    assert not link_needs_check([], NOW)


# --- «сейчас дешевле на …» ---


def test_cheaper_now_per_unit() -> None:
    lamp = item(seen(NOW - timedelta(hours=1), 90_000))

    assert cheaper_now(bought(3, 100_000, quantity=2), item_price_at(lamp, NOW), NOW) == 10_000


def test_not_cheaper_or_same_price_is_none() -> None:
    assert cheaper_now(bought(3, 100_000), item_price_at(item(seen(NOW - timedelta(hours=1), 100_000)), NOW), NOW) is None
    assert cheaper_now(bought(3, 100_000), item_price_at(item(seen(NOW - timedelta(hours=1), 110_000)), NOW), NOW) is None


def test_cheaper_stops_after_14_days() -> None:
    price = item_price_at(item(seen(NOW - timedelta(hours=1), 50_000)), NOW)

    assert cheaper_now(bought(14 - 1 / 86_400, 100_000), price, NOW) == 50_000
    assert cheaper_now(bought(14, 100_000), price, NOW) is None


def test_cheaper_ignores_stale_and_missing_prices() -> None:
    stale = item_price_at(item(seen(NOW - STALE_AFTER, 50_000)), NOW)
    gone = item_price_at(item(seen(NOW - timedelta(hours=1), None)), NOW)

    assert cheaper_now(bought(3), stale, NOW) is None
    assert cheaper_now(bought(3), gone, NOW) is None


# --- цена в момент добавления ---


def test_baseline_is_known_price_at_adding() -> None:
    added = NOW - timedelta(days=5)
    lamp = item(seen(added - timedelta(days=1), 120_000), seen(added + timedelta(days=1), 90_000), since=added)

    assert baseline_price(lamp, added, NOW) == 120_000


def test_baseline_is_first_confirmed_check_after_adding() -> None:
    added = NOW - timedelta(days=5)
    lamp = item(
        seen(added + timedelta(hours=1), 1_000, Confirmation.PENDING),
        seen(added + timedelta(hours=2), None),
        seen(added + timedelta(hours=3), 130_000),
        seen(added + timedelta(hours=4), 90_000),
        since=added,
    )

    assert baseline_price(lamp, added, NOW) == 130_000


def test_baseline_is_best_among_stores() -> None:
    added = NOW - timedelta(days=5)
    other = Binding(LinkInput(2, 2, True, (seen(added + timedelta(hours=1), 110_000),)), added, None)
    lamp = item(seen(added + timedelta(hours=1), 120_000), since=added, more=(other,))

    assert baseline_price(lamp, added, NOW) == 110_000


def test_baseline_ignores_stale_price_at_adding() -> None:
    added = NOW - timedelta(days=5)
    lamp = item(seen(added - STALE_AFTER, 50_000), seen(added + timedelta(hours=1), 100_000), since=added)

    assert baseline_price(lamp, added, NOW) == 100_000


def test_no_baseline_without_price_before_purchase() -> None:
    added = NOW - timedelta(days=5)
    lamp = item(seen(NOW - timedelta(hours=1), 100_000), since=added)

    assert baseline_price(lamp, added, NOW - timedelta(days=1)) is None
    assert baseline_price(item(since=added), added, NOW) is None


# --- статистика ---


def record(days_ago: float, price: int, quantity: int = 1, baseline: int | None = None, category: int = 1) -> PurchaseRecord:
    return PurchaseRecord(1, category, Purchase(NOW - timedelta(days=days_ago), price, quantity, 1), baseline)


def test_spent_and_savings_for_period() -> None:
    records = [
        record(1, 90_000, quantity=2, baseline=100_000),  # экономия 20 000
        record(3, 55_000, baseline=50_000),  # переплата 5 000
        record(5, 10_000),  # цены при добавлении не было — в экономию не входит
        record(40, 1_000_000, baseline=2_000_000),  # вне месяца
    ]

    assert purchase_stats(records, Period.MONTH, NOW) == PurchaseStats(3, 245_000, 15_000)
    assert purchase_stats(records, Period.YEAR, NOW) == PurchaseStats(4, 1_245_000, 1_015_000)


def test_period_boundary_is_moscow_midnight() -> None:
    at_midnight = PurchaseRecord(1, 1, Purchase(TODAY, 100, 1, 1), None)
    before = PurchaseRecord(2, 1, Purchase(TODAY - timedelta(microseconds=1), 200, 1, 1), None)

    assert purchase_stats([at_midnight, before], Period.DAY, NOW) == PurchaseStats(1, 100, None)


def test_overpayment_only_gives_negative_savings() -> None:
    assert purchase_stats([record(0.1, 120_000, baseline=100_000)], Period.DAY, NOW).savings_minor == -20_000


def test_empty_stats() -> None:
    assert purchase_stats([], Period.MONTH, NOW) == PurchaseStats(0, 0, None)


def test_stats_by_category() -> None:
    records = [record(1, 100, category=1), record(2, 200, baseline=300, category=2), record(400, 999, category=3)]

    assert purchase_stats_by_category(records, Period.MONTH, NOW) == {
        1: PurchaseStats(1, 100, None),
        2: PurchaseStats(1, 200, 100),
    }
