"""pricewatch.domain.review: смена названия, скачок цены, вердикт и автоматическое закрытие."""

from datetime import UTC, datetime

import pytest

from pricewatch.domain.pricing import Availability, Confirmation, Observation
from pricewatch.domain.review import (
    ReviewKind,
    Thresholds,
    closes_price_review,
    price_jumped,
    review,
    title_changed,
)

# --- название ---


def test_exactly_85_percent_similarity_is_not_suspicious() -> None:
    # Схожесть 2·17/40 = 0,85 ровно.
    assert not title_changed("a" * 20, "a" * 17 + "bbb")


def test_below_85_percent_similarity_is_suspicious() -> None:
    # Схожесть 2·16/40 = 0,80.
    assert title_changed("a" * 20, "a" * 16 + "bbbb")


@pytest.mark.parametrize(
    "title",
    ["ЛАМПА НАСТОЛЬНАЯ ЁЖИК 40 Вт", "лампа настольная ежик 40 вт", "  Лампа   настольная Ёжик 40 Вт ", "Ｌампа настольная Ёжик 40 Вт"],
)
def test_case_yo_spaces_and_width_do_not_change_title(title: str) -> None:
    assert not title_changed("Лампа настольная Ёжик 40 Вт", title)


def test_small_edit_is_not_suspicious() -> None:
    assert not title_changed("Смартфон Galaxy A55 8/256 ГБ синий", "Смартфон Galaxy A55 8/256 ГБ, синий")


def test_different_product_is_suspicious() -> None:
    assert title_changed("Смартфон Galaxy A55 8/256 ГБ синий", "Чехол-книжка для Galaxy A55")


LONG_TITLE = (
    "мм гб набор кабель смартфон гб pro кабель вт синий для стали синий кабель набор смартфон мм мм стали a55 "
    "смартфон 2 для usb-c 256 черный чехол max 2 256 256 смартфон galaxy мм синий a55 usb-c 8 черный гб"
)


def test_long_titles_are_compared_without_junk_heuristic() -> None:
    # Для строк от 200 знаков difflib по умолчанию считает частые символы «мусором»: здесь вставка
    # одной буквы дала бы схожесть 31 % вместо 99,8 %, и товар ушёл бы на проверку зря.
    assert not title_changed(LONG_TITLE, LONG_TITLE[:121] + "x" + LONG_TITLE[121:])


def test_empty_titles_are_not_suspicious() -> None:
    assert not title_changed("", "   ")


def test_title_threshold_is_a_parameter() -> None:
    strict = Thresholds(title_similarity_min_percent=90)
    assert title_changed("a" * 20, "a" * 17 + "bbb", strict)


# --- цена ---


@pytest.mark.parametrize(
    ("new", "jumped"),
    [(130_000, False), (130_001, True), (70_000, False), (69_999, True), (100_000, False), (300_000, True), (1, True)],
)
def test_price_jump_boundary_is_30_percent(new: int, jumped: bool) -> None:
    assert price_jumped(100_000, new) is jumped


def test_price_jump_exact_on_odd_amounts() -> None:
    # 30 % от 333 копеек = 99,9: 99 копеек — в пределах, 100 — уже скачок.
    assert not price_jumped(333, 432)
    assert price_jumped(333, 433)


def test_card_price_is_what_is_compared() -> None:
    # Обычная цена не изменилась, но по карте стала на 40 % ниже — это скачок.
    card = Observation(datetime(2026, 10, 6, tzinfo=UTC), None, 100_000, 60_000, Availability.IN_STOCK, Confirmation.CONFIRMED)

    assert card.effective_price_minor == 60_000
    assert review("Лампа", 100_000, "Лампа", card.effective_price_minor).kinds == {ReviewKind.PRICE_JUMP}


def test_price_threshold_is_a_parameter() -> None:
    assert not price_jumped(100_000, 140_000, Thresholds(price_jump_max_percent=40))


@pytest.mark.parametrize(("previous", "new"), [(0, 100), (100, 0), (-1, 100)])
def test_price_jump_needs_positive_prices(previous: int, new: int) -> None:
    with pytest.raises(ValueError):
        price_jumped(previous, new)


@pytest.mark.parametrize("bad", [100.0, True, "100"])
def test_price_jump_rejects_non_integer(bad: object) -> None:
    with pytest.raises(TypeError):
        price_jumped(bad, 100)  # type: ignore[arg-type]


# --- вердикт ---


def test_ordinary_check_is_confirmed() -> None:
    verdict = review("Лампа", 100_000, "лампа", 101_000)

    assert verdict.kinds == frozenset()
    assert verdict.confirmation is Confirmation.CONFIRMED


def test_both_suspicions_wait_for_decision() -> None:
    verdict = review("Смартфон Galaxy A55", 100_000, "Чехол для телефона", 50_000)

    assert verdict.kinds == {ReviewKind.TITLE_CHANGED, ReviewKind.PRICE_JUMP}
    assert verdict.confirmation is Confirmation.PENDING


def test_title_only_suspicion() -> None:
    assert review("Смартфон Galaxy A55", 100_000, "Чехол для телефона", 100_000).kinds == {ReviewKind.TITLE_CHANGED}


@pytest.mark.parametrize(
    ("baseline", "last_price", "title", "price"),
    [
        (None, 100_000, "Что угодно", 100_000),  # эталона названия ещё нет
        ("Лампа", None, "Лампа", 1),  # первая цена — сравнивать не с чем
        ("Лампа", 100_000, "Лампа", None),  # нет в наличии — цены нет
        ("Лампа", 100_000, None, 100_000),  # магазин не отдал название
    ],
)
def test_nothing_to_compare_is_not_suspicious(baseline: str | None, last_price: int | None, title: str | None, price: int | None) -> None:
    assert review(baseline, last_price, title, price).confirmation is Confirmation.CONFIRMED


def test_kind_values_match_api_contract() -> None:
    assert {kind.value for kind in ReviewKind} == {"title_changed", "price_jump"}


# --- автоматическое закрытие ---


def test_price_review_closes_when_next_check_is_within_threshold() -> None:
    assert closes_price_review(100_000, 105_000)
    assert closes_price_review(100_000, 130_000)


def test_price_review_stays_open_when_jump_repeats() -> None:
    assert not closes_price_review(100_000, 40_000)


def test_out_of_stock_does_not_close_price_review() -> None:
    assert not closes_price_review(100_000, None)


# --- пороги ---


@pytest.mark.parametrize(
    ("title", "price", "error"),
    [(0, 30, ValueError), (101, 30, ValueError), (85, 0, ValueError), (85.0, 30, TypeError), (85, True, TypeError)],
)
def test_invalid_thresholds_rejected(title: object, price: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        Thresholds(title, price)  # type: ignore[arg-type]
