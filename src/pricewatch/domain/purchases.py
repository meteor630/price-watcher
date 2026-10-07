"""Покупки и архив (DEVELOPMENT_ARCHITECTURE.md, раздел 6).

- Переходы: в списке → куплен, в списке → в архиве, и обратно «Вернуть в список».
  Купленный товар в архив переводится через список: это разные решения пользователя.
- Покупка: цена за штуку в целых копейках, количество 1–999, магазин (может быть
  не из списка — тогда без магазина), дата не позже текущего момента.
- Ссылки купленного товара проверяются ещё 14 суток после покупки; ровно 14 суток —
  уже нет. Архивные не проверяются.
- «Сейчас дешевле на …» — разница за штуку, пока цена проверяется и актуальна.
- Статистика за период: потрачено — цена × количество; экономия — (цена в момент
  добавления − цена покупки) × количество, со знаком (минус — переплата).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.domain.days import Period, period_start
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import check_quantity
from pricewatch.domain.pricing import STALE_AFTER, ItemPrice
from pricewatch.domain.shopping import ItemState, Purchase
from pricewatch.domain.totals import TotalItem, item_price_at

TRACK_AFTER_PURCHASE = timedelta(days=14)
# 10 млрд ₽: с количеством до 999 сумма заведомо помещается в bigint базы.
PURCHASE_PRICE_MAX = 10**12

TRANSITIONS = frozenset(
    {
        (ItemState.ACTIVE, ItemState.PURCHASED),
        (ItemState.ACTIVE, ItemState.ARCHIVED),
        (ItemState.PURCHASED, ItemState.ACTIVE),
        (ItemState.ARCHIVED, ItemState.ACTIVE),
    }
)


@dataclass(frozen=True, slots=True)
class PurchaseRecord:
    """Покупка для статистики; `baseline_minor` — лучшая цена в момент добавления товара, если была."""

    item_id: int
    category_id: int
    purchase: Purchase
    baseline_minor: int | None


@dataclass(frozen=True, slots=True)
class PurchaseStats:
    count: int
    spent_minor: int
    # None — ни у одной покупки периода не было цены в момент добавления товара.
    savings_minor: int | None


def change_state(current: ItemState, target: ItemState) -> ItemState:
    if (current, target) not in TRANSITIONS:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"state": "invalid_transition"}})
    return target


def new_purchase(price_minor: int, quantity: int, store_id: int | None, purchased_at: datetime, now: datetime) -> Purchase:
    """Данные формы «Куплено» или VALIDATION_ERROR с причиной по полю."""
    if isinstance(price_minor, bool) or not isinstance(price_minor, int) or not 0 < price_minor <= PURCHASE_PRICE_MAX:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"purchase_price": "out_of_range"}})
    check_quantity(quantity, field="purchase_quantity")
    if purchased_at.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("время без часового пояса")
    if purchased_at > now:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"purchased_at": "in_future"}})
    return Purchase(purchased_at, price_minor, quantity, store_id)


def is_tracked(state: ItemState, purchased_at: datetime | None, now: datetime) -> bool:
    """Нужно ли ещё проверять ссылки товара."""
    if state is ItemState.ACTIVE:
        return True
    if state is ItemState.PURCHASED:
        if purchased_at is None:
            raise ValueError("у купленного товара нет даты покупки")
        return now - purchased_at < TRACK_AFTER_PURCHASE
    return False


def link_needs_check(items: Iterable[tuple[ItemState, datetime | None]], now: datetime) -> bool:
    """Общую ссылку проверяют, пока её отслеживает хоть один товар любого пользователя."""
    return any(is_tracked(state, purchased_at, now) for state, purchased_at in items)


def cheaper_now(purchase: Purchase, current: ItemPrice, now: datetime) -> int | None:
    """На сколько копеек за штуку товар сейчас дешевле цены покупки; `None` — не дешевле или не следим."""
    if now - purchase.purchased_at >= TRACK_AFTER_PURCHASE or not current.participates or current.price_minor is None:
        return None
    difference = purchase.price_minor - current.price_minor
    return difference if difference > 0 else None


def baseline_price(item: TotalItem, added_at: datetime, until: datetime, stale_after: timedelta = STALE_AFTER) -> int | None:
    """Лучшая цена в момент добавления: первая актуальная цена товара начиная с `added_at`.

    Ссылка общая, поэтому её цена может быть известна уже в момент добавления; иначе берётся
    первая подтверждённая проверка после него, но не позже `until` (обычно — момента покупки).
    """
    moments = sorted(
        {added_at}
        | {
            observation.checked_at
            for binding in item.bindings
            for observation in binding.link.observations
            if observation.is_success and added_at < observation.checked_at <= until
        }
    )
    for moment in moments:
        price = item_price_at(item, moment, stale_after)
        if price.participates:
            return price.price_minor
    return None


def purchase_stats(records: Iterable[PurchaseRecord], period: Period, now: datetime) -> PurchaseStats:
    start = period_start(period, now)
    count = spent = 0
    savings: int | None = None
    for record in records:
        purchase = record.purchase
        if not start <= purchase.purchased_at <= now:
            continue
        count += 1
        spent += purchase.price_minor * purchase.quantity
        if record.baseline_minor is not None:
            savings = (savings or 0) + (record.baseline_minor - purchase.price_minor) * purchase.quantity
    return PurchaseStats(count, spent, savings)


def purchase_stats_by_category(records: Sequence[PurchaseRecord], period: Period, now: datetime) -> dict[int, PurchaseStats]:
    """Статистика по категориям; категории без покупок за период не попадают в ответ."""
    by_category: dict[int, list[PurchaseRecord]] = {}
    for record in records:
        by_category.setdefault(record.category_id, []).append(record)
    stats = {category: purchase_stats(group, period, now) for category, group in by_category.items()}
    return {category: value for category, value in stats.items() if value.count}
