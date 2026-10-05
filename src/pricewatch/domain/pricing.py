"""Цена ссылки и цена товара (DEVELOPMENT_ARCHITECTURE.md, раздел 6).

Действующая цена — по карте, если она есть, иначе обычная. Ссылка участвует
в расчётах по последнему успешному подтверждённому наблюдению: магазин включён,
товар по нему в наличии, наблюдению меньше `stale_after` (ровно 7 суток — уже
не актуально). Сбои после него показываются с причиной, но цену не убирают.
Неподтверждённые наблюдения (ждут решения) цену не меняют и дают признак
«на проверке»; отклонённые не учитываются вовсе.

Расчёт идёт на момент `now`: наблюдения позже него не видны, поэтому та же
функция отвечает и на вопрос «какой была цена тогда».
"""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pricewatch.domain.errors import ErrorCode

STALE_AFTER = timedelta(days=7)


class PriceStatus(StrEnum):
    OK = "ok"
    CHECK_FAILED = "check_failed"
    STALE = "stale"
    OUT_OF_STOCK = "out_of_stock"
    AWAITING_CHECK = "awaiting_check"
    STORE_DISABLED = "store_disabled"
    NO_LINKS = "no_links"


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


class Confirmation(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Observation:
    """Результат одной проверки ссылки. `failure is None` — проверка успешна.

    Успешная проверка без отметки «нет в наличии» обязана содержать обычную цену:
    магазин её показал. Неизвестное наличие при найденной цене считается наличием.
    Сбой не несёт цен и не ждёт решения.
    """

    checked_at: datetime
    failure: ErrorCode | None
    price_minor: int | None
    card_price_minor: int | None
    availability: Availability
    confirmation: Confirmation

    def __post_init__(self) -> None:
        if self.checked_at.utcoffset() is None:
            raise ValueError("время проверки без часового пояса")
        for value in (self.price_minor, self.card_price_minor):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError("цены — целые копейки")
            if value is not None and value <= 0:
                raise ValueError("цена должна быть положительной")
        if self.failure is not None:
            if self.price_minor is not None or self.card_price_minor is not None:
                raise ValueError("у сбоя нет цены")
            if self.confirmation is not Confirmation.CONFIRMED:
                raise ValueError("сбой не ждёт решения")
        elif self.availability is not Availability.OUT_OF_STOCK and self.price_minor is None:
            raise ValueError("успешная проверка товара в наличии без цены")

    @property
    def is_success(self) -> bool:
        return self.failure is None and self.confirmation is Confirmation.CONFIRMED

    @property
    def effective_price_minor(self) -> int | None:
        """Цена по карте, если есть, иначе обычная; `None`, если товара нет в наличии."""
        if self.availability is Availability.OUT_OF_STOCK:
            return None
        return self.card_price_minor if self.card_price_minor is not None else self.price_minor


@dataclass(frozen=True, slots=True)
class LinkInput:
    """Ссылка, привязанная к товару на момент расчёта, и все её наблюдения."""

    link_id: int
    store_id: int
    store_enabled: bool
    observations: Sequence[Observation]

    def __post_init__(self) -> None:
        # Сортировка один раз: расчёт на момент ищет границу двоичным поиском (графики за год — 365 расчётов).
        object.__setattr__(self, "observations", tuple(sorted(self.observations, key=_checked_at)))


@dataclass(frozen=True, slots=True)
class LinkPrice:
    link_id: int
    store_id: int
    status: PriceStatus
    # Последнее успешное подтверждённое наблюдение; по нему показывается цена.
    observation: Observation | None
    price_minor: int | None
    participates: bool
    # Причина сбоя последней проверки, если после успешной (или вместо неё) проверка не удалась.
    failure: ErrorCode | None
    under_review: bool


@dataclass(frozen=True, slots=True)
class ItemPrice:
    status: PriceStatus
    # Лучшая участвующая ссылка; для stale и store_disabled — ссылка с последней известной ценой.
    best: LinkPrice | None
    price_minor: int | None
    # Цена актуальна и может идти в итог (отметку «в итоге» учитывает расчёт итога).
    participates: bool
    under_review: bool
    links: tuple[LinkPrice, ...]


def link_price(link: LinkInput, now: datetime, stale_after: timedelta = STALE_AFTER) -> LinkPrice:
    if now.utcoffset() is None:
        raise ValueError("время расчёта без часового пояса")
    observations = link.observations
    success: Observation | None = None
    # Последняя проверка, влияющая на показ: сбой или подтверждённый успех; ждущие решения и отклонённые — нет.
    last_check: Observation | None = None
    pending_times = []
    # Назад от момента `now` до последнего успешного наблюдения; более ранние на результат не влияют.
    for index in range(bisect_right(observations, now, key=_checked_at) - 1, -1, -1):
        observation = observations[index]
        if last_check is None and (observation.failure is not None or observation.is_success):
            last_check = observation
        if observation.is_success:
            success = observation
            break
        if observation.confirmation is Confirmation.PENDING:
            pending_times.append(observation.checked_at)
    failure = last_check.failure if last_check is not None else None
    under_review = any(success is None or moment > success.checked_at for moment in pending_times)

    price = success.effective_price_minor if success is not None else None
    if not link.store_enabled:
        status = PriceStatus.STORE_DISABLED
    elif success is None:
        status = PriceStatus.AWAITING_CHECK
    elif now - success.checked_at >= stale_after:
        status = PriceStatus.STALE
    elif price is None:
        status = PriceStatus.OUT_OF_STOCK
    elif failure is not None:
        status = PriceStatus.CHECK_FAILED
    else:
        status = PriceStatus.OK
    participates = status in (PriceStatus.OK, PriceStatus.CHECK_FAILED)
    return LinkPrice(link.link_id, link.store_id, status, success, price, participates, failure, under_review)


def item_price(links: Sequence[LinkInput], now: datetime, stale_after: timedelta = STALE_AFTER) -> ItemPrice:
    """Лучшая цена и статус товара по ссылкам, привязанным к нему на момент `now`."""
    prices = tuple(link_price(link, now, stale_after) for link in links)
    under_review = any(price.under_review for price in prices)
    if not prices:
        return ItemPrice(PriceStatus.NO_LINKS, None, None, False, under_review, prices)

    participating = [price for price in prices if price.participates]
    if participating:
        best = _cheapest(participating)
        status = PriceStatus.CHECK_FAILED if best.failure is not None else PriceStatus.OK
        return ItemPrice(status, best, best.price_minor, True, under_review, prices)

    if any(price.status is PriceStatus.OUT_OF_STOCK for price in prices):
        return ItemPrice(PriceStatus.OUT_OF_STOCK, None, None, False, under_review, prices)
    for status in (PriceStatus.STALE, PriceStatus.STORE_DISABLED):
        checked = [price for price in prices if price.status is status and price.observation is not None]
        if checked:
            # Последняя известная цена; её нет, если та проверка сказала «нет в наличии».
            known = [price for price in checked if price.price_minor is not None]
            best = _cheapest(known) if known else None
            return ItemPrice(status, best, best.price_minor if best else None, False, under_review, prices)
    return ItemPrice(PriceStatus.AWAITING_CHECK, None, None, False, under_review, prices)


def _checked_at(observation: Observation) -> datetime:
    return observation.checked_at


def _cheapest(prices: Sequence[LinkPrice]) -> LinkPrice:
    # При равной цене выбор устойчив: меньший номер магазина, затем ссылки.
    return min(prices, key=lambda price: (price.price_minor, price.store_id, price.link_id))
