"""pricewatch.domain.review: смена названия, скачок цены, вердикт и автоматическое закрытие."""

from datetime import UTC, datetime, timedelta

import pytest

from pricewatch.domain.errors import ErrorCode
from pricewatch.domain.pricing import Availability, Confirmation, Observation
from pricewatch.domain.review import (
    Checked,
    Decision,
    ReviewKind,
    Thresholds,
    UserContext,
    closes_price_review,
    price_jumped,
    resolve_for_user,
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


# --- решения пользователя: что засчитано именно ему ---

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
TITLE = "Монитор LG 27GR93U"


def obs(observation_id: int, hours: int, price: int | None, confirmation: Confirmation = Confirmation.CONFIRMED, title: str | None = TITLE,
        failure: ErrorCode | None = None) -> Checked:
    availability = Availability.OUT_OF_STOCK if price is None and failure is None else Availability.IN_STOCK
    observation = Observation(T0 + timedelta(hours=hours), failure, price, None, availability, confirmation)
    return Checked(observation_id, title, observation)


def seen_as(checked: list[Checked], decisions: dict[int, Decision] | None = None, since: datetime = T0, title: str | None = TITLE) -> list[str]:
    resolved = resolve_for_user(checked, UserContext(title, since, decisions or {}))
    return [observation.confirmation.value for observation in resolved]


PENDING, REJECTED = Confirmation.PENDING, Confirmation.REJECTED


def test_confirmed_counts_for_everyone_and_jump_waits() -> None:
    history = [obs(1, 1, 30_000), obs(2, 2, 45_000, PENDING)]

    assert seen_as(history) == ["confirmed", "pending"]


def test_accept_counts_and_next_close_price_counts_by_itself() -> None:
    # Пример 1 из плана: принял 45 000 ₽ — следующий 45 200 ₽ засчитан сам; у другого пользователя ждёт решения.
    history = [obs(1, 1, 30_000), obs(2, 2, 45_000, PENDING), obs(3, 26, 45_200, PENDING)]

    assert seen_as(history, {2: Decision.ACCEPT}) == ["confirmed", "confirmed", "confirmed"]
    assert seen_as(history) == ["confirmed", "pending", "pending"]


def test_reject_keeps_old_price_and_new_jumps_still_wait() -> None:
    history = [obs(1, 1, 30_000), obs(2, 2, 45_000, PENDING), obs(3, 26, 45_200, PENDING), obs(4, 50, 30_500)]

    assert seen_as(history, {2: Decision.REJECT}) == ["confirmed", "rejected", "pending", "confirmed"]


def test_decision_on_latest_closes_earlier_undecided() -> None:
    history = [obs(1, 1, 30_000), obs(2, 2, 45_000, PENDING), obs(3, 26, 46_000, PENDING), obs(4, 27, 60_000, PENDING)]

    assert seen_as(history, {3: Decision.ACCEPT}) == ["confirmed", "rejected", "confirmed", "pending"]  # 60 000 от 46 000 — +30,4 %
    assert seen_as(history, {4: Decision.REJECT}) == ["confirmed", "rejected", "rejected", "rejected"]


def test_before_watching_counts_by_itself() -> None:
    # Пример 2: ссылку добавили, когда скачок уже был, — спрашивать про прошлое не нужно.
    history = [obs(1, 1, 30_000), obs(2, 2, 45_000, PENDING), obs(3, 30, 45_100, PENDING), obs(4, 31, 90_000, PENDING)]

    assert seen_as(history, since=T0 + timedelta(hours=10)) == ["confirmed", "confirmed", "confirmed", "pending"]


def test_auto_closed_is_not_counted_unless_accepted() -> None:
    history = [obs(1, 1, 30_000), obs(2, 2, 45_000, REJECTED), obs(3, 3, 30_100)]

    assert seen_as(history) == ["confirmed", "rejected", "confirmed"]
    assert seen_as(history, since=T0 + timedelta(hours=10)) == ["confirmed", "rejected", "confirmed"]
    assert seen_as(history, {2: Decision.ACCEPT}) == ["confirmed", "confirmed", "confirmed"]


def test_accepted_title_becomes_users_baseline() -> None:
    renamed = "Холодильник Атлант ХМ-4021"
    history = [obs(1, 1, 30_000), obs(2, 2, 30_000, PENDING, title=renamed), obs(3, 26, 30_100, PENDING, title=renamed)]

    assert seen_as(history) == ["confirmed", "pending", "pending"]
    assert seen_as(history, {2: Decision.ACCEPT}) == ["confirmed", "confirmed", "confirmed"]
    assert seen_as(history, title=renamed) == ["confirmed", "confirmed", "confirmed"]  # эталон пользователя — уже новое название


def test_out_of_stock_and_failures_pass_through() -> None:
    history = [obs(1, 1, 30_000), obs(2, 2, None), obs(3, 3, None, failure=ErrorCode.TIMEOUT), obs(4, 4, 31_000, PENDING)]

    assert seen_as(history) == ["confirmed", "confirmed", "confirmed", "confirmed"]  # 31 000 от 30 000 — в пределах порога


def test_exactly_on_threshold_counts_for_user() -> None:
    history = [obs(1, 1, 30_000), obs(2, 2, 45_000, PENDING), obs(3, 26, 58_500, PENDING)]

    assert seen_as(history, {2: Decision.ACCEPT}) == ["confirmed", "confirmed", "confirmed"]  # ровно +30 % от 45 000 — не подозрительно
