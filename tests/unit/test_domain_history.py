"""История ссылки: смена цены, нет в наличии и снова в наличии, сбой после успеха; неподтверждённое не в счёт."""

from datetime import UTC, datetime, timedelta

from pricewatch.domain.errors import ErrorCode
from pricewatch.domain.history import EventKind, link_history
from pricewatch.domain.pricing import Availability, Confirmation, Observation

START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def seen(day: int, price: int | None = None, card: int | None = None, availability: Availability = Availability.IN_STOCK,
         failure: ErrorCode | None = None, confirmation: Confirmation = Confirmation.CONFIRMED) -> Observation:
    return Observation(START + timedelta(days=day), failure, price, card, availability, confirmation)


def kinds_and_texts(observations: list[Observation]) -> list[tuple[EventKind, str]]:
    return [(event.kind, event.text) for event in link_history(observations)]


def test_price_changes_use_card_price() -> None:
    events = kinds_and_texts([seen(0, 12_990_00), seen(1, 12_990_00), seen(2, 12_500_00, card=11_490_00)])

    assert events == [(EventKind.PRICE_CHANGED, "12 990\xa0₽ → 11 490\xa0₽")]


def test_stock_goes_and_comes_back() -> None:
    events = kinds_and_texts([seen(0, 1000_00), seen(1, availability=Availability.OUT_OF_STOCK), seen(2, 900_00)])

    assert events == [
        (EventKind.OUT_OF_STOCK, "Нет в наличии"),
        (EventKind.BACK_IN_STOCK, "Снова в наличии"),
        (EventKind.PRICE_CHANGED, "1 000\xa0₽ → 900\xa0₽"),
    ]


def test_repeated_failures_are_one_event() -> None:
    events = kinds_and_texts([seen(0, 1000_00), seen(1, failure=ErrorCode.CAPTCHA), seen(2, failure=ErrorCode.CAPTCHA), seen(3, 1000_00)])

    assert [kind for kind, _ in events] == [EventKind.CHECK_FAILED]
    assert events[0][1].startswith("Не удалось проверить: ")


def test_pending_and_rejected_do_not_change_history() -> None:
    events = kinds_and_texts([
        seen(0, 1000_00), seen(1, 100_00, confirmation=Confirmation.PENDING), seen(2, 100_00, confirmation=Confirmation.REJECTED), seen(3, 1000_00),
    ])

    assert events == []


def test_first_observation_is_not_an_event_and_order_is_by_time() -> None:
    assert link_history([seen(1, 900_00), seen(0, 1000_00)])[0].at == START + timedelta(days=1)
    assert link_history([seen(0, failure=ErrorCode.TIMEOUT)]) == []
