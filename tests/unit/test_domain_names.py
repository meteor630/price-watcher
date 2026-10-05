"""pricewatch.domain.names: очистка имён, ключ сравнения, количество."""

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import (
    CATEGORY_NAME_MAX,
    ITEM_FULL_NAME_MAX,
    check_quantity,
    clean_name,
    name_key,
)


def reason(error: PriceWatcherError, field: str = "name") -> str:
    assert error.code is ErrorCode.VALIDATION_ERROR
    return error.details["fields"][field]  # type: ignore[index]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Компьютер", "Компьютер"),
        ("  Мой   дом \t", "Мой дом"),
        ("Рабочее место", "Рабочее место"),
        ("Ёлка", "Ёлка"),
        ("й", "й"),
    ],
)
def test_clean_name_keeps_case_and_yo(raw: str, clean: str) -> None:
    assert clean_name(raw, field="name", max_length=CATEGORY_NAME_MAX) == clean


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Компьютер", "КОМПЬЮТЕР"),
        ("Ёлка", "елка"),
        ("ЁЛКА", "Елка"),
        ("Мой дом", "  мой    дом "),
        ("ёж", "ёж"),
        ("ＡＢＣ", "abc"),
        ("Straße", "STRASSE"),
        ("йод", "йод"),
    ],
)
def test_same_key_for_typos_in_case_space_and_yo(first: str, second: str) -> None:
    assert name_key(first) == name_key(second)


@pytest.mark.parametrize(("first", "second"), [("Мониторы", "Монитор"), ("Дом", "Дым"), ("е", "э")])
def test_different_names_have_different_keys(first: str, second: str) -> None:
    assert name_key(first) != name_key(second)


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", " "])
def test_empty_name_rejected(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        clean_name(raw, field="name", max_length=CATEGORY_NAME_MAX)

    assert reason(caught.value) == "empty"


def test_length_counted_after_cleaning() -> None:
    assert len(clean_name("  " + "я" * CATEGORY_NAME_MAX + "  ", field="name", max_length=CATEGORY_NAME_MAX)) == 100

    with pytest.raises(PriceWatcherError) as caught:
        clean_name("я" * (CATEGORY_NAME_MAX + 1), field="name", max_length=CATEGORY_NAME_MAX)

    assert reason(caught.value) == "too_long"


def test_full_name_limit_and_field_name() -> None:
    assert clean_name("я" * ITEM_FULL_NAME_MAX, field="full_name", max_length=ITEM_FULL_NAME_MAX)

    with pytest.raises(PriceWatcherError) as caught:
        clean_name("я" * (ITEM_FULL_NAME_MAX + 1), field="full_name", max_length=ITEM_FULL_NAME_MAX)

    assert reason(caught.value, "full_name") == "too_long"


@pytest.mark.parametrize("raw", ["Компь\x00ютер", "звон\x07ок", "a\x1bb"])
def test_control_characters_rejected(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        clean_name(raw, field="name", max_length=CATEGORY_NAME_MAX)

    assert reason(caught.value) == "invalid_characters"


@pytest.mark.parametrize("value", [1, 2, 999])
def test_quantity_in_range(value: int) -> None:
    assert check_quantity(value) == value


@pytest.mark.parametrize("value", [0, -1, 1000, True, False, 1.0, "2"])
def test_quantity_out_of_range_or_wrong_type(value: object) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        check_quantity(value)  # type: ignore[arg-type]

    assert reason(caught.value, "quantity") == "out_of_range"
