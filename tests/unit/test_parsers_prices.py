"""pricewatch.parsers.prices: строгий разбор структурированных цен и кандидаты из текста."""

from decimal import Decimal

import pytest

from pricewatch.parsers.prices import CANDIDATES_MAX, is_ruble, minor_from_decimal, structured_price, text_candidates


@pytest.mark.parametrize(
    ("value", "minor"),
    [
        (12990, 1_299_000),
        ("12990", 1_299_000),
        ("12990.00", 1_299_000),
        ("12990.5", 1_299_050),
        (Decimal("12990.50"), 1_299_050),
        ("12 990", 1_299_000),
        ("12 990,50", 1_299_050),
        ("12 990", 1_299_000),
        ("12.99", 1_299),  # точка — десятичный разделитель, а не разряды
        ("0.01", 1),
        (" 100 ", 10_000),
    ],
)
def test_structured_price(value: object, minor: int) -> None:
    assert structured_price(value) == minor


@pytest.mark.parametrize(
    "value",
    [0, "0", "0.00", -5, "-5", "12.999", Decimal("0.001"), "по запросу", "", "12,990.00", "1e5", "NaN", "Infinity",
     True, 12990.5, None, [1], "12 990 ₽"],
)
def test_not_a_structured_price(value: object) -> None:
    with pytest.raises(ValueError):
        structured_price(value)


def test_float_is_never_accepted() -> None:
    # JSON-LD читается с parse_float=Decimal: float сюда попасть не должен вовсе.
    with pytest.raises(ValueError):
        structured_price(0.1 + 0.2)


def test_minor_from_decimal_exact() -> None:
    assert minor_from_decimal(Decimal("1299.9")) == 129_990
    with pytest.raises(ValueError):
        minor_from_decimal(Decimal("NaN"))


@pytest.mark.parametrize(("currency", "ruble"), [(None, True), ("RUB", True), ("rub", True), (" RUR ", True), ("USD", False), ("EUR", False), ("", False)])
def test_is_ruble(currency: str | None, ruble: bool) -> None:
    assert is_ruble(currency) is ruble


def test_text_candidates_in_order_without_duplicates() -> None:
    texts = ["Цена 1 990 ₽", "12 990 руб.", "3 500 р.", "999 RUB", "1 990 ₽", "Скидка 500 руб"]

    assert text_candidates(texts) == (199_000, 1_299_000, 350_000, 99_900, 50_000)


@pytest.mark.parametrize(
    "text",
    [
        "Артикул 123456",  # price-parser считает «р» в слове рублём
        "Доставка завтра, 2 шт",
        "Рейтинг 4.8 из 5",
        "Отзывы 1 200",
        "$19.99",
        "1.299,00 €",
        "Цена по запросу",
        "0 ₽",
        "Кешбэк 300 баллов",
    ],
)
def test_text_without_ruble_price_gives_nothing(text: str) -> None:
    assert text_candidates([text]) == ()


def test_text_candidates_are_limited() -> None:
    texts = [f"{price} ₽" for price in range(100, 120)]

    assert len(text_candidates(texts)) == CANDIDATES_MAX
