"""Цены товаров для страниц: загрузка с привязками и наблюдениями, строка товара для таблиц.

Общая часть главной, категории и группы. Товары берутся «в списке» (`active`); цена сейчас
и в начале периода считается по правилам domain.totals, изменение — по цене за штуку, если она
учтена в обоих моментах. Наблюдения читаются только с нужного окна (PriceDataRepository.observations).
"""

from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.ports import PricedItem, StoreInfo, UnitOfWork
from pricewatch.domain.days import Period, period_start
from pricewatch.domain.money import Change, format_rub, price_change
from pricewatch.domain.pricing import ItemPrice, LinkInput, LinkPrice, PriceStatus
from pricewatch.domain.shopping import ItemState
from pricewatch.domain.totals import BEFORE, Binding, TotalItem, in_total_shown, item_price_at


@dataclass(frozen=True, slots=True)
class StoreView:
    store_id: int
    name: str
    color_token: str


@dataclass(frozen=True, slots=True)
class PricedRow:
    """Товар «в списке» с ценой сейчас и в начале периода."""

    item: PricedItem
    total_item: TotalItem
    now: ItemPrice
    before: ItemPrice
    change: Change | None


@dataclass(frozen=True, slots=True)
class ItemRowView:
    """Строка товара в таблице (схема ItemRow): цены готовыми строками, статус цены, лучший магазин."""

    item_id: int
    version: int
    name: str
    group_id: int | None
    quantity: int
    in_total: bool
    price_status: PriceStatus
    under_review: bool
    best_store: StoreView | None
    unit_price_minor: int | None
    unit_price_text: str | None
    line_price_minor: int | None
    line_price_text: str | None
    regular_price_text: str | None
    change: Change | None
    checked_at: datetime | None


@dataclass(frozen=True, slots=True)
class PricedList:
    rows: list[PricedRow]
    stores: dict[int, StoreInfo]


def load(
    uow: UnitOfWork, user_id: int, period: Period, now: datetime, since: datetime, *, item_ids: frozenset[int] | None = None
) -> PricedList:
    """Товары пользователя; `since` — самый ранний момент, на который нужна цена (графики, история).

    Без `item_ids` — товары «в списке»; с ними — только эти товары в любом состоянии (карточка товара).
    """
    before_moment = period_start(period, now) - BEFORE
    if item_ids is None:
        items = [item for item in uow.price_data.items(user_id) if item.state is ItemState.ACTIVE]
    else:
        items = [item for item in uow.price_data.items(user_id) if item.item_id in item_ids]
    wanted = {item.item_id for item in items}
    bindings = [binding for binding in uow.price_data.bindings(user_id) if binding.item_id in wanted]
    stores = uow.price_data.stores(sorted({binding.store_id for binding in bindings}))
    history = uow.price_data.observations(sorted({binding.tracked_link_id for binding in bindings}), min(since, before_moment))
    by_item: dict[int, list[Binding]] = {}
    for binding in bindings:
        link = LinkInput(binding.tracked_link_id, binding.store_id, stores[binding.store_id].enabled, history[binding.tracked_link_id])
        by_item.setdefault(binding.item_id, []).append(Binding(link, binding.valid_from, binding.valid_to))
    rows = []
    for item in items:
        total_item = TotalItem(item.item_id, item.state, item.quantity, item.in_total, item.include_stale, by_item.get(item.item_id, []))
        now_price = item_price_at(total_item, now)
        before_price = item_price_at(total_item, before_moment)
        change = None
        if now_price.participates and before_price.participates and now_price.price_minor is not None and before_price.price_minor is not None:
            change = price_change(before_price.price_minor, now_price.price_minor)
        rows.append(PricedRow(item, total_item, now_price, before_price, change))
    return PricedList(rows, stores)


def store_view(stores: dict[int, StoreInfo], link: LinkPrice | None) -> StoreView | None:
    if link is None:
        return None
    store = stores[link.store_id]
    return StoreView(store.store_id, store.name, store.color_token)


def price_text(price_minor: int | None) -> str | None:
    return None if price_minor is None else format_rub(price_minor)


def item_row(row: PricedRow, stores: dict[int, StoreInfo]) -> ItemRowView:
    price = row.now
    unit = price.price_minor
    line = None if unit is None else unit * row.item.quantity
    observation = price.best.observation if price.best is not None else None
    regular = None
    if observation is not None and observation.card_price_minor is not None and observation.price_minor is not None:
        regular = format_rub(observation.price_minor)
    return ItemRowView(
        item_id=row.item.item_id,
        version=row.item.version,
        name=row.item.name,
        group_id=row.item.group_id,
        quantity=row.item.quantity,
        in_total=in_total_shown(row.total_item, price),
        price_status=price.status,
        under_review=price.under_review,
        best_store=store_view(stores, price.best),
        unit_price_minor=unit,
        unit_price_text=price_text(unit),
        line_price_minor=line,
        line_price_text=price_text(line),
        regular_price_text=regular,
        change=row.change,
        checked_at=None if observation is None else observation.checked_at,
    )
