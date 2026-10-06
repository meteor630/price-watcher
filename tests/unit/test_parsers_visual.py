"""pricewatch.parsers.visual: выбор главного ценника и наличие по кнопкам — на готовых данных зонда."""

import pytest

from pricewatch.domain.pricing import Availability
from pricewatch.parsers.visual import VisualPrice, availability, pick_price


def candidate(amount: str, *, size: float = 16, weight: int = 400, dy: int = 50, dx: int = 0, struck: bool = False, bad: bool = False) -> dict:
    return {"amount": amount, "size": size, "weight": weight, "dy": dy, "dx": dx, "struck": struck, "bad": bad}


def probe(*cands: dict, h1: str | None = "Лампа", buy: bool = False, oos: bool = False) -> dict:
    return {"cands": list(cands), "h1": h1, "buy": buy, "oos": oos, "image": None}


def test_biggest_bold_price_near_title_wins() -> None:
    chosen = pick_price(probe(
        candidate("1 990", size=14, dy=40),  # мелкая цена рядом
        candidate("12 990", size=32, weight=700, dy=80),  # крупная жирная — цена товара
        candidate("990", size=32, weight=700, dy=1400),  # такая же крупная, но далеко внизу
    ))

    assert chosen == VisualPrice(1_299_000, 32, True)


@pytest.mark.parametrize("trap", [{"struck": True}, {"bad": True}, {"dy": 1500}])
def test_struck_installment_and_far_prices_are_skipped(trap: dict) -> None:
    chosen = pick_price(probe(candidate("9 990", size=40, weight=700, **trap), candidate("12 990", size=20)))

    assert chosen is not None and chosen.price_minor == 1_299_000


@pytest.mark.parametrize(
    ("amount", "minor"), [("12 990", 1_299_000), ("12 990,50", 1_299_050), ("99", 9_900), ("1 299 990", 129_999_000)]
)
def test_amount_to_kopecks(amount: str, minor: int) -> None:
    chosen = pick_price(probe(candidate(amount)))

    assert chosen is not None and chosen.price_minor == minor


@pytest.mark.parametrize(
    "value",
    [
        None,
        "строка",
        probe(h1=None),  # без заголовка не понять, чья цена
        probe(),  # ценников нет
        probe(candidate("0")),
        probe(candidate("abc")),
        probe(candidate("100", dy="далеко")),  # type: ignore[arg-type]
        {"cands": "не список", "h1": "Лампа"},
        {"cands": ["не словарь"], "h1": "Лампа"},
    ],
)
def test_nothing_usable_gives_none(value: object) -> None:
    assert pick_price(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (probe(buy=True), Availability.IN_STOCK),
        (probe(buy=True, oos=True), Availability.IN_STOCK),  # кнопка покупки важнее надписи в соседнем блоке
        (probe(oos=True), Availability.OUT_OF_STOCK),
        (probe(), Availability.UNKNOWN),
        (None, Availability.UNKNOWN),
    ],
)
def test_availability_from_buttons(value: object, expected: Availability) -> None:
    assert availability(value) is expected
