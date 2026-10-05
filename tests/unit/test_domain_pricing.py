"""pricewatch.domain.pricing: действующая цена, участие ссылки, лучшая цена и статус товара."""

from datetime import UTC, datetime, timedelta

import pytest

from pricewatch.domain.errors import ErrorCode
from pricewatch.domain.pricing import (
    STALE_AFTER,
    Availability,
    Confirmation,
    LinkInput,
    Observation,
    PriceStatus,
    item_price,
    link_price,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def ok(
    price: int | None = 100_000,
    *,
    ago: timedelta = timedelta(hours=1),
    card: int | None = None,
    availability: Availability = Availability.IN_STOCK,
    confirmation: Confirmation = Confirmation.CONFIRMED,
) -> Observation:
    return Observation(NOW - ago, None, price, card, availability, confirmation)


def gone(ago: timedelta = timedelta(hours=1)) -> Observation:
    return ok(None, ago=ago, availability=Availability.OUT_OF_STOCK)


def failed(code: ErrorCode = ErrorCode.TIMEOUT, ago: timedelta = timedelta(minutes=10)) -> Observation:
    return Observation(NOW - ago, code, None, None, Availability.UNKNOWN, Confirmation.CONFIRMED)


def link(*observations: Observation, link_id: int = 1, store_id: int = 1, enabled: bool = True) -> LinkInput:
    return LinkInput(link_id, store_id, enabled, observations)


# --- действующая цена ---


def test_card_price_wins_over_regular() -> None:
    assert ok(100_000, card=95_000).effective_price_minor == 95_000
    assert ok(100_000).effective_price_minor == 100_000


def test_card_price_even_if_higher_than_regular() -> None:
    # Правило раздела 6 — «по карте, если она есть», без сравнения.
    assert ok(100_000, card=101_000).effective_price_minor == 101_000


def test_out_of_stock_has_no_effective_price() -> None:
    assert ok(100_000, card=90_000, availability=Availability.OUT_OF_STOCK).effective_price_minor is None


def test_unknown_availability_with_price_counts_as_in_stock() -> None:
    assert link_price(link(ok(availability=Availability.UNKNOWN)), NOW).participates


# --- участие ссылки и граница 7 суток ---


def test_fresh_success_participates() -> None:
    price = link_price(link(ok(100_000)), NOW)

    assert (price.status, price.price_minor, price.participates, price.failure) == (PriceStatus.OK, 100_000, True, None)


def test_just_under_seven_days_participates() -> None:
    assert link_price(link(ok(ago=STALE_AFTER - timedelta(seconds=1))), NOW).participates


def test_exactly_seven_days_is_stale() -> None:
    price = link_price(link(ok(ago=STALE_AFTER)), NOW)

    assert price.status is PriceStatus.STALE
    assert not price.participates
    assert price.price_minor == 100_000  # последняя цена остаётся для показа


def test_stale_after_is_a_parameter() -> None:
    assert link_price(link(ok(ago=timedelta(days=3))), NOW, stale_after=timedelta(days=3)).status is PriceStatus.STALE


def test_failure_after_success_keeps_price_and_reason() -> None:
    price = link_price(link(ok(100_000, ago=timedelta(days=2)), failed(ErrorCode.CAPTCHA)), NOW)

    assert price.status is PriceStatus.CHECK_FAILED
    assert price.participates
    assert price.price_minor == 100_000
    assert price.failure is ErrorCode.CAPTCHA


def test_success_after_failure_clears_reason() -> None:
    price = link_price(link(failed(ago=timedelta(hours=2)), ok(ago=timedelta(hours=1))), NOW)

    assert (price.status, price.failure) == (PriceStatus.OK, None)


def test_failures_only_is_awaiting_with_reason() -> None:
    price = link_price(link(failed(ErrorCode.LAYOUT_CHANGED)), NOW)

    assert (price.status, price.participates, price.failure) == (PriceStatus.AWAITING_CHECK, False, ErrorCode.LAYOUT_CHANGED)


def test_no_observations_is_awaiting() -> None:
    assert link_price(link(), NOW).status is PriceStatus.AWAITING_CHECK


def test_out_of_stock_does_not_participate() -> None:
    price = link_price(link(ok(100_000, ago=timedelta(days=1)), gone()), NOW)

    assert (price.status, price.price_minor, price.participates) == (PriceStatus.OUT_OF_STOCK, None, False)


def test_back_in_stock_participates_again() -> None:
    assert link_price(link(gone(timedelta(days=1)), ok(90_000)), NOW).price_minor == 90_000


def test_disabled_store_shows_last_price_but_does_not_participate() -> None:
    price = link_price(link(ok(100_000), enabled=False), NOW)

    assert (price.status, price.price_minor, price.participates) == (PriceStatus.STORE_DISABLED, 100_000, False)


def test_observations_order_does_not_matter() -> None:
    early, late = ok(100_000, ago=timedelta(days=2)), ok(90_000, ago=timedelta(days=1))

    assert link_price(link(late, early), NOW).price_minor == 90_000


def test_observations_after_now_are_invisible() -> None:
    future = Observation(NOW + timedelta(seconds=1), None, 50_000, None, Availability.IN_STOCK, Confirmation.CONFIRMED)

    assert link_price(link(ok(100_000), future), NOW).price_minor == 100_000
    assert link_price(link(future), NOW).status is PriceStatus.AWAITING_CHECK


# --- подтверждение ---


def test_pending_keeps_confirmed_price_and_marks_review() -> None:
    price = link_price(link(ok(100_000, ago=timedelta(days=1)), ok(40_000, confirmation=Confirmation.PENDING)), NOW)

    assert (price.price_minor, price.under_review, price.status) == (100_000, True, PriceStatus.OK)


def test_pending_does_not_refresh_staleness() -> None:
    price = link_price(link(ok(100_000, ago=timedelta(days=8)), ok(40_000, confirmation=Confirmation.PENDING)), NOW)

    assert price.status is PriceStatus.STALE
    assert price.under_review


def test_pending_without_any_confirmed_is_awaiting_under_review() -> None:
    price = link_price(link(ok(40_000, confirmation=Confirmation.PENDING)), NOW)

    assert (price.status, price.under_review) == (PriceStatus.AWAITING_CHECK, True)


def test_rejected_is_ignored() -> None:
    price = link_price(link(ok(100_000, ago=timedelta(days=1)), ok(1_000, confirmation=Confirmation.REJECTED)), NOW)

    assert (price.price_minor, price.under_review) == (100_000, False)


def test_review_ends_with_newer_confirmed_success() -> None:
    price = link_price(
        link(ok(40_000, ago=timedelta(days=1), confirmation=Confirmation.PENDING), ok(100_000, ago=timedelta(hours=1))), NOW
    )

    assert not price.under_review


# --- товар ---


def test_best_price_is_minimum_of_participating() -> None:
    price = item_price(
        [
            link(ok(120_000), link_id=1, store_id=1),
            link(ok(110_000, card=99_000), link_id=2, store_id=2),
            link(ok(50_000, ago=STALE_AFTER), link_id=3, store_id=3),  # дешевле, но устарела
            link(ok(10_000), link_id=4, store_id=4, enabled=False),  # дешевле, но магазин выключен
        ],
        NOW,
    )

    assert (price.status, price.price_minor, price.participates) == (PriceStatus.OK, 99_000, True)
    assert price.best is not None and price.best.store_id == 2
    assert [p.status for p in price.links] == [PriceStatus.OK, PriceStatus.OK, PriceStatus.STALE, PriceStatus.STORE_DISABLED]


def test_equal_prices_choose_stable_store() -> None:
    price = item_price([link(ok(100_000), link_id=7, store_id=5), link(ok(100_000), link_id=3, store_id=2)], NOW)

    assert price.best is not None and price.best.store_id == 2


def test_failed_check_of_best_link_is_check_failed_with_price() -> None:
    price = item_price([link(ok(90_000, ago=timedelta(days=1)), failed(), store_id=1), link(ok(100_000), link_id=2, store_id=2)], NOW)

    assert (price.status, price.price_minor) == (PriceStatus.CHECK_FAILED, 90_000)


def test_failed_check_of_other_link_keeps_ok() -> None:
    price = item_price([link(ok(90_000), store_id=1), link(ok(100_000, ago=timedelta(days=1)), failed(), link_id=2, store_id=2)], NOW)

    assert price.status is PriceStatus.OK


def test_no_links() -> None:
    price = item_price([], NOW)

    assert (price.status, price.best, price.price_minor, price.participates) == (PriceStatus.NO_LINKS, None, None, False)


@pytest.mark.parametrize(
    ("links", "status", "price_minor"),
    [
        # нет в наличии важнее устаревшей цены
        ([link(gone()), link(ok(ago=STALE_AFTER), link_id=2, store_id=2)], PriceStatus.OUT_OF_STOCK, None),
        # устаревшая важнее выключенного магазина
        ([link(ok(80_000, ago=STALE_AFTER)), link(ok(70_000), link_id=2, store_id=2, enabled=False)], PriceStatus.STALE, 80_000),
        # выключенный магазин важнее ожидания
        ([link(), link(ok(70_000), link_id=2, store_id=2, enabled=False)], PriceStatus.STORE_DISABLED, 70_000),
        ([link(), link(failed(), link_id=2, store_id=2)], PriceStatus.AWAITING_CHECK, None),
        # выключенный магазин без единой проверки — это ещё ожидание
        ([link(enabled=False)], PriceStatus.AWAITING_CHECK, None),
    ],
)
def test_status_priority_when_nothing_participates(links: list[LinkInput], status: PriceStatus, price_minor: int | None) -> None:
    price = item_price(links, NOW)

    assert (price.status, price.price_minor, price.participates) == (status, price_minor, False)


def test_stale_out_of_stock_is_stale_without_price() -> None:
    price = item_price([link(gone(STALE_AFTER))], NOW)

    assert (price.status, price.best, price.price_minor) == (PriceStatus.STALE, None, None)


def test_disabled_store_out_of_stock_is_store_disabled_without_price() -> None:
    price = item_price([link(gone(), enabled=False)], NOW)

    assert (price.status, price.price_minor) == (PriceStatus.STORE_DISABLED, None)


def test_stale_picks_cheapest_last_price() -> None:
    price = item_price([link(ok(90_000, ago=timedelta(days=9))), link(ok(80_000, ago=timedelta(days=8)), link_id=2, store_id=2)], NOW)

    assert (price.status, price.price_minor) == (PriceStatus.STALE, 80_000)


def test_item_under_review_if_any_link_is() -> None:
    price = item_price([link(ok(90_000)), link(ok(80_000), ok(1, ago=timedelta(minutes=5), confirmation=Confirmation.PENDING), link_id=2, store_id=2)], NOW)

    assert price.under_review
    assert price.price_minor == 80_000


def test_price_at_past_moment() -> None:
    history = link(ok(100_000, ago=timedelta(days=3)), ok(90_000, ago=timedelta(days=1)))

    assert item_price([history], NOW - timedelta(days=2)).price_minor == 100_000
    assert item_price([history], NOW - timedelta(days=4)).status is PriceStatus.AWAITING_CHECK


def test_status_values_match_api_contract() -> None:
    assert {status.value for status in PriceStatus} == {
        "ok", "check_failed", "stale", "out_of_stock", "awaiting_check", "store_disabled", "no_links"
    }


# --- отказ на неверных данных ---


@pytest.mark.parametrize(
    "build",
    [
        lambda: Observation(NOW, None, 0, None, Availability.IN_STOCK, Confirmation.CONFIRMED),
        lambda: Observation(NOW, None, -5, None, Availability.IN_STOCK, Confirmation.CONFIRMED),
        lambda: Observation(NOW, None, 100, 0, Availability.IN_STOCK, Confirmation.CONFIRMED),
        lambda: Observation(NOW, None, None, None, Availability.IN_STOCK, Confirmation.CONFIRMED),
        lambda: Observation(NOW, None, None, 100, Availability.UNKNOWN, Confirmation.CONFIRMED),
        lambda: Observation(NOW, ErrorCode.TIMEOUT, 100, None, Availability.UNKNOWN, Confirmation.CONFIRMED),
        lambda: Observation(NOW, ErrorCode.TIMEOUT, None, None, Availability.UNKNOWN, Confirmation.PENDING),
        lambda: Observation(datetime(2026, 10, 6, 12, 0), None, 100, None, Availability.IN_STOCK, Confirmation.CONFIRMED),
    ],
)
def test_invalid_observation_rejected(build) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        build()


@pytest.mark.parametrize("bad", [999.0, True, "100"])
def test_observation_price_must_be_integer(bad: object) -> None:
    with pytest.raises(TypeError):
        Observation(NOW, None, bad, None, Availability.IN_STOCK, Confirmation.CONFIRMED)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Observation(NOW, None, 100, bad, Availability.IN_STOCK, Confirmation.CONFIRMED)  # type: ignore[arg-type]


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError):
        link_price(link(ok()), datetime(2026, 10, 6, 12, 0))


def test_link_keeps_observations_sorted_and_immutable() -> None:
    late, early = ok(ago=timedelta(hours=1)), ok(ago=timedelta(days=1))
    source = [late, early]

    stored = link(*source).observations
    source.clear()

    assert stored == (early, late)
