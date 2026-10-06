"""Подозрительные изменения (DEVELOPMENT_ARCHITECTURE.md, раздел 6).

- Название: схожесть нормализованных названий ниже порога (85 % по умолчанию).
  Регистр, «ё» и лишние пробелы не делают название подозрительным.
- Цена: действующая цена (по карте, если есть) отличается от последней
  подтверждённой больше чем на порог (30 % по умолчанию).

Ровно на пороге — не подозрительно: в архитектуре сказано «больше чем».
Сравнения точные, в целых числах: граница не зависит от погрешности float.
Подозрительное наблюдение ждёт решения администратора; ценовое подозрение
закрывается само, если следующая проверка вернула цену в пределы порога.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from pricewatch.domain.names import name_key
from pricewatch.domain.pricing import Confirmation

TITLE_SIMILARITY_MIN_PERCENT = 85
PRICE_JUMP_MAX_PERCENT = 30


class ReviewKind(StrEnum):
    TITLE_CHANGED = "title_changed"
    PRICE_JUMP = "price_jump"


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Пороги из настроек администратора, в целых процентах."""

    title_similarity_min_percent: int = TITLE_SIMILARITY_MIN_PERCENT
    price_jump_max_percent: int = PRICE_JUMP_MAX_PERCENT

    def __post_init__(self) -> None:
        for value in (self.title_similarity_min_percent, self.price_jump_max_percent):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("пороги — целые проценты")
        if not 1 <= self.title_similarity_min_percent <= 100:
            raise ValueError("порог схожести названий — от 1 до 100 %")
        if self.price_jump_max_percent < 1:
            raise ValueError("порог скачка цены — не меньше 1 %")


@dataclass(frozen=True, slots=True)
class Review:
    kinds: frozenset[ReviewKind]

    @property
    def confirmation(self) -> Confirmation:
        return Confirmation.PENDING if self.kinds else Confirmation.CONFIRMED


def title_changed(baseline: str, title: str, thresholds: Thresholds = Thresholds()) -> bool:
    """Схожесть нормализованных названий ниже порога."""
    first, second = name_key(baseline), name_key(title)
    total = len(first) + len(second)
    if total == 0:
        return False
    # autojunk=False: для строк от 200 знаков difflib иначе считает частые символы «мусором»,
    # и почти одинаковые длинные названия выглядят непохожими.
    matched = sum(block.size for block in SequenceMatcher(None, first, second, autojunk=False).get_matching_blocks())
    # Схожесть 2·matched/total < порог/100, без деления.
    return 200 * matched < thresholds.title_similarity_min_percent * total


def price_jumped(previous_minor: int, new_minor: int, thresholds: Thresholds = Thresholds()) -> bool:
    """Новая цена отличается от прежней подтверждённой больше чем на порог."""
    for value in (previous_minor, new_minor):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("цены — целые копейки")
        if value <= 0:
            raise ValueError("цена должна быть положительной")
    return abs(new_minor - previous_minor) * 100 > thresholds.price_jump_max_percent * previous_minor


def review(
    baseline_title: str | None,
    last_confirmed_price_minor: int | None,
    title: str | None,
    price_minor: int | None,
    thresholds: Thresholds = Thresholds(),
) -> Review:
    """Вердикт по успешной проверке: какие изменения ждут решения.

    `price_minor` — действующая цена новой проверки, `None`, если товара нет в наличии.
    Нет эталона или прежней цены — сравнивать не с чем, изменение не подозрительно.
    """
    kinds = set()
    if baseline_title is not None and title is not None and title_changed(baseline_title, title, thresholds):
        kinds.add(ReviewKind.TITLE_CHANGED)
    if (
        last_confirmed_price_minor is not None
        and price_minor is not None
        and price_jumped(last_confirmed_price_minor, price_minor, thresholds)
    ):
        kinds.add(ReviewKind.PRICE_JUMP)
    return Review(frozenset(kinds))


def closes_price_review(
    last_confirmed_price_minor: int, new_price_minor: int | None, thresholds: Thresholds = Thresholds()
) -> bool:
    """Открытое ценовое подозрение закрывается само, если новая проверка вернула цену в пределы порога.

    Проверка без цены (нет в наличии) подозрение не закрывает. Подозрение на смену
    названия так не закрывается: его решает только администратор.
    """
    return new_price_minor is not None and not price_jumped(last_confirmed_price_minor, new_price_minor, thresholds)
