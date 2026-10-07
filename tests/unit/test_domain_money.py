"""pricewatch.domain.money: рубли из копеек, изменения, проценты без float."""

from decimal import Decimal

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import (
    Change, Direction, format_change, format_percent, format_rub, input_rub, parse_rub, percent_tenths, price_change,
)

NNBSP = " "
NBSP = " "


@pytest.mark.parametrize(
    ("minor", "text"),
    [
        (0, "0 ₽"),
        (1, "0,01 ₽"),
        (99, "0,99 ₽"),
        (100, "1 ₽"),
        (99_900, "999 ₽"),
        (100_000, f"1{NNBSP}000 ₽"),
        (1_299_000, f"12{NNBSP}990 ₽"),
        (1_299_050, f"12{NNBSP}990,50 ₽"),
        (1_299_005, f"12{NNBSP}990,05 ₽"),
        (123_456_789_00, f"123{NNBSP}456{NNBSP}789 ₽"),
        (-65_200, f"−652 ₽"),
    ],
)
def test_format_rub(minor: int, text: str) -> None:
    assert format_rub(minor) == text.replace(" ₽", f"{NBSP}₽")


def test_format_rub_never_breaks_between_digits_and_currency() -> None:
    text = format_rub(1_234_567_00)

    assert " " not in text  # только неразрывные пробелы
    assert text.endswith(f"{NBSP}₽")


@pytest.mark.parametrize(
    ("minor", "text"),
    [(-65_200, "−652"), (11_700, "+117"), (0, "0"), (-1, "−0,01"), (1_000_000_00, f"+1{NNBSP}000{NNBSP}000")],
)
def test_format_change_signs(minor: int, text: str) -> None:
    assert format_change(minor) == f"{text}{NBSP}₽"


@pytest.mark.parametrize(
    ("change", "base", "tenths"),
    [
        (0, 1000, 0),
        (1, 1000, 1),  # 0,1% ровно
        (5, 10_000, 1),  # 0,05% → 0,1%: половина вверх
        (4, 10_000, 0),  # 0,04% → 0,0%
        (-5, 10_000, 1),  # знак не влияет на округление
        (15, 10_000, 2),  # 0,15% → 0,2%
        (14, 10_000, 1),
        (1, 3, 333),  # 33,33…% → 33,3%
        (2, 3, 667),  # 66,66…% → 66,7%
        (1000, 1000, 1000),
        (3000, 1000, 3000),
    ],
)
def test_percent_tenths_rounds_half_up(change: int, base: int, tenths: int) -> None:
    assert percent_tenths(change, base) == tenths


def test_percent_exact_on_huge_amounts() -> None:
    # float на таких числах уже теряет точность; целочисленный расчёт — нет.
    base = 10**17 + 1
    assert percent_tenths(base // 2000 + 1, base) == 1  # чуть больше 0,05%
    assert percent_tenths(base // 2000 - 1, base) == 0


@pytest.mark.parametrize(("tenths", "text"), [(0, "0,0%"), (1, "0,1%"), (10, "1,0%"), (125, "12,5%"), (123_450, f"12{NNBSP}345,0%")])
def test_format_percent(tenths: int, text: str) -> None:
    assert format_percent(tenths) == text


def test_price_change_down_up_flat() -> None:
    assert price_change(1_299_000, 1_233_800) == Change(Direction.DOWN, -65_200, f"−652{NBSP}₽", "5,0%")
    assert price_change(100_000, 111_700) == Change(Direction.UP, 11_700, f"+117{NBSP}₽", "11,7%")
    assert price_change(100_000, 100_000) == Change(Direction.FLAT, 0, f"0{NBSP}₽", "0,0%")


def test_price_change_to_zero_is_full_drop() -> None:
    assert price_change(100, 0) == Change(Direction.DOWN, -100, f"−1{NBSP}₽", "100,0%")


@pytest.mark.parametrize("before", [0, -100])
def test_price_change_needs_positive_base(before: int) -> None:
    with pytest.raises(ValueError):
        price_change(before, 100)


@pytest.mark.parametrize("base", [0, -1])
def test_percent_needs_positive_base(base: int) -> None:
    with pytest.raises(ValueError):
        percent_tenths(1, base)


def test_format_percent_rejects_negative() -> None:
    with pytest.raises(ValueError):
        format_percent(-1)


@pytest.mark.parametrize("bad", [12.5, 1299.0, Decimal("12.50"), True, "100", None])
@pytest.mark.parametrize(
    "call",
    [
        format_rub,
        format_change,
        format_percent,
        lambda value: percent_tenths(value, 100),
        lambda value: percent_tenths(1, value),
        lambda value: price_change(value, 100),
        lambda value: price_change(100, value),
    ],
)
def test_money_rejects_non_integer_amounts(call, bad: object) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TypeError):
        call(bad)


@pytest.mark.parametrize(
    ("text", "minor"),
    [
        ("12990", 1_299_000),
        ("12 990,50 ₽", 1_299_050),
        ("12\u202f990,5", 1_299_050),
        ("0,01", 1),
        ("  7.05 ", 705),
        ("999999999999,99", 99_999_999_999_999),
    ],
)
def test_parse_rub_accepts_human_input(text: str, minor: int) -> None:
    assert parse_rub(text, "price") == minor


@pytest.mark.parametrize("text", ["", "₽", "-5", "1,234", "1e3", "12,5,0", "1000000000000", "１２", "12 990.50.1", "NaN"])
def test_parse_rub_refuses_everything_else(text: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        parse_rub(text, "price")

    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert caught.value.details == {"fields": {"price": "invalid"}}


@pytest.mark.parametrize(("minor", "text"), [(1_299_000, "12990"), (1_299_050, "12990,50"), (5, "0,05")])
def test_input_rub_round_trips(minor: int, text: str) -> None:
    assert input_rub(minor) == text
    assert parse_rub(input_rub(minor), "price") == minor
