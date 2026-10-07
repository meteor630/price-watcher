"""Итог, изменение за период и минимум периода (DEVELOPMENT_ARCHITECTURE.md, раздел 6).

Ссылки привязаны к товару на отрезок `[valid_from, valid_to)`. Цена товара на момент
берётся по ссылкам, привязанным в этот момент, поэтому замена и отключение ссылки
не обрывают историю. В итог идут только товары в списке (`active`) с отметкой
«в итоге»: цена участвует, либо она устарела, а пользователь снова включил отметку
(`include_stale`). Изменение за период считается «только из-за цен»: по товарам,
у которых цена учтена в обоих моментах; новый товар не выдаётся за подорожание.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.domain.days import Period, period_start
from pricewatch.domain.money import Change, price_change
from pricewatch.domain.names import check_quantity
from pricewatch.domain.pricing import STALE_AFTER, ItemPrice, LinkInput, PriceStatus, item_price
from pricewatch.domain.shopping import ItemState

# «Последняя подтверждённая до 00:00»: момент сразу перед началом периода (точность datetime — микросекунда).
BEFORE = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class Binding:
    link: LinkInput
    valid_from: datetime
    valid_to: datetime | None

    def __post_init__(self) -> None:
        if self.valid_from.utcoffset() is None or (self.valid_to is not None and self.valid_to.utcoffset() is None):
            raise ValueError("время привязки без часового пояса")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("привязка заканчивается раньше, чем начинается")

    def active_at(self, moment: datetime) -> bool:
        return self.valid_from <= moment and (self.valid_to is None or moment < self.valid_to)


@dataclass(frozen=True, slots=True)
class TotalItem:
    """Товар для расчёта итога: сегодняшние количество и отметки, все привязки ссылок."""

    item_id: int
    state: ItemState
    quantity: int
    in_total: bool
    include_stale: bool
    bindings: Sequence[Binding]
    # Срок актуальности цены из настроек администратора (stale_after_days); по умолчанию 7 суток.
    stale_after: timedelta = STALE_AFTER

    def __post_init__(self) -> None:
        check_quantity(self.quantity)


@dataclass(frozen=True, slots=True)
class PeriodMinimum:
    price_minor: int
    checked_at: datetime
    store_id: int


def item_price_at(item: TotalItem, moment: datetime) -> ItemPrice:
    return item_price([binding.link for binding in item.bindings if binding.active_at(moment)], moment, item.stale_after)


def in_total_shown(item: TotalItem, price: ItemPrice) -> bool:
    """Состояние отметки «в итоге»: при устаревшей цене она снята, пока пользователь её снова не включит."""
    if price.status is PriceStatus.STALE:
        return item.in_total and item.include_stale
    return item.in_total


def counted_unit_price(item: TotalItem, price: ItemPrice) -> int | None:
    """Цена за штуку, которая идёт в итог, или `None`."""
    if item.state is not ItemState.ACTIVE or not in_total_shown(item, price):
        return None
    if price.participates or price.status is PriceStatus.STALE:
        return price.price_minor
    return None


def total(items: Sequence[TotalItem], now: datetime) -> int:
    """Сумма «цена за штуку × количество» по товарам, которые идут в итог."""
    return sum(
        unit * item.quantity
        for item in items
        if (unit := counted_unit_price(item, item_price_at(item, now))) is not None
    )


def total_change(items: Sequence[TotalItem], period: Period, now: datetime) -> Change | None:
    """Изменение итога с начала периода только из-за цен; `None`, если сравнить не с чем."""
    before_moment = period_start(period, now) - BEFORE
    before_sum = after_sum = 0
    for item in items:
        after = counted_unit_price(item, item_price_at(item, now))
        before = counted_unit_price(item, item_price_at(item, before_moment))
        if after is not None and before is not None:
            before_sum += before * item.quantity
            after_sum += after * item.quantity
    if before_sum == 0:
        return None
    return price_change(before_sum, after_sum)


def period_minimum(item: TotalItem, period: Period, now: datetime) -> PeriodMinimum | None:
    """Самая низкая подтверждённая цена за штуку за период среди включённых магазинов.

    Учитываются только успешные подтверждённые наблюдения «в наличии», сделанные
    за время привязки ссылки к товару. При равной цене — более раннее наблюдение.
    """
    start = period_start(period, now)
    candidates = [
        PeriodMinimum(price, observation.checked_at, binding.link.store_id)
        for binding in item.bindings
        if binding.link.store_enabled
        for observation in binding.link.observations
        if observation.is_success
        and start <= observation.checked_at <= now
        and binding.active_at(observation.checked_at)
        and (price := observation.effective_price_minor) is not None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (candidate.price_minor, candidate.checked_at, candidate.store_id))
