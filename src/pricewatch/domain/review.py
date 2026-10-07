"""Подозрительные изменения (DEVELOPMENT_ARCHITECTURE.md, раздел 6).

- Название: схожесть нормализованных названий ниже порога (85 % по умолчанию).
  Регистр, «ё» и лишние пробелы не делают название подозрительным.
- Цена: действующая цена (по карте, если есть) отличается от последней
  подтверждённой больше чем на порог (30 % по умолчанию).

Ровно на пороге — не подозрительно: в архитектуре сказано «больше чем».
Сравнения точные, в целых числах: граница не зависит от погрешности float.
Ценовое подозрение закрывается само, если следующая проверка вернула цену в пределы порога.

Решают пользователи — каждый только за свои товары (решение владельца 07.10.2026). Проверка ссылки
общая, а засчитано ли подозрительное наблюдение, у каждого своё (`resolve_for_user`):

1. Наблюдение, прошедшее проверку для всех (confirmed), засчитывается всем.
2. Подозрительное (pending): принято пользователем — засчитано ему; отклонено — нет; без решения,
   но в пределах порога от его последней засчитанной цены и его эталона названия — засчитано само;
   иначе ждёт его решения.
3. Решение по наблюдению закрывает более ранние нерешённые подозрительные той же ссылки: они отклонены.
4. Подозрительные наблюдения до того, как пользователь начал следить за ссылкой, засчитываются ему сами;
   закрытые автоматически (rejected) — нет.
5. Явное «принять» важнее автоматического закрытия (rejected).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from difflib import SequenceMatcher
from enum import StrEnum

from pricewatch.domain.names import name_key
from pricewatch.domain.pricing import Confirmation, Observation

TITLE_SIMILARITY_MIN_PERCENT = 85
PRICE_JUMP_MAX_PERCENT = 30


class Decision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


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


@dataclass(frozen=True, slots=True)
class Checked:
    """Наблюдение ссылки с номером и названием — то, о чём пользователь принимает решение."""

    observation_id: int
    title: str | None
    observation: Observation


@dataclass(frozen=True, slots=True)
class UserContext:
    """Что пользователь решил по ссылке.

    `baseline_title` — его эталон названия: последнее принятое им название или общий эталон ссылки.
    `watching_since` — с какого момента он следит за ссылкой (первая привязка к его товару).
    """

    baseline_title: str | None
    watching_since: datetime
    decisions: Mapping[int, Decision]


def resolve_for_user(checked: Sequence[Checked], context: UserContext, thresholds: Thresholds = Thresholds()) -> tuple[Observation, ...]:
    """Наблюдения ссылки так, как их видит пользователь: засчитанные — confirmed, отклонённые — rejected,
    ждущие его решения — pending. Порядок — по времени проверки."""
    ordered = sorted(checked, key=lambda item: (item.observation.checked_at, item.observation_id))
    decided_up_to = max((index for index, item in enumerate(ordered) if item.observation_id in context.decisions), default=-1)
    last_price: int | None = None
    title = context.baseline_title
    resolved = []
    for index, item in enumerate(ordered):
        observation = item.observation
        confirmation = observation.confirmation
        decision = context.decisions.get(item.observation_id)
        if observation.failure is None and confirmation is not Confirmation.CONFIRMED:
            before_watching = confirmation is Confirmation.PENDING and observation.checked_at < context.watching_since
            if decision is Decision.ACCEPT or before_watching:
                confirmation = Confirmation.CONFIRMED
            elif decision is Decision.REJECT or confirmation is Confirmation.REJECTED or index < decided_up_to:
                confirmation = Confirmation.REJECTED
            else:
                confirmation = review(title, last_price, item.title, observation.effective_price_minor, thresholds).confirmation
            if confirmation is Confirmation.CONFIRMED and item.title is not None:
                title = item.title
        if observation.failure is None and confirmation is Confirmation.CONFIRMED and observation.effective_price_minor is not None:
            last_price = observation.effective_price_minor
        resolved.append(observation if confirmation is observation.confirmation else replace(observation, confirmation=confirmation))
    return tuple(resolved)
