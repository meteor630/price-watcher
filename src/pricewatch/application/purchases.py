"""Покупка и архив товара (DEVELOPMENT_ARCHITECTURE.md, раздел 6; SITE_DESIGN_SPEC.md, раздел 6).

- «Куплено»: цена за штуку (по умолчанию — лучшая сейчас), количество (по умолчанию — товара), магазин из ссылок
  товара (в том числе прежних) или без магазина, дата не позже сегодня. Момент покупки — 00:00 по Москве
  выбранного дня: с ним совпадают границы периодов статистики, 14 суток проверки считаются от дня покупки.
- При покупке запоминается лучшая цена в момент добавления товара (domain.purchases.baseline_price): экономия
  не пересчитывается задним числом.
- «В архив» и «Вернуть в список» — по версии записи. Покупку и архив можно отменить токеном (15 секунд).
- Страница «Покупки» (SITE_DESIGN_SPEC.md, раздел 6а): за период — «потрачено» и экономия (domain.purchases),
  разбивка по категориям и список покупок с экономией каждой; архив — товары с последней известной ценой.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from pricewatch.application.ports import StoreInfo, UnitOfWork
from pricewatch.application.price_view import ItemRowView, StoreView, item_row, load, only_row
from pricewatch.domain.accounts import User
from pricewatch.domain.days import Period, day_start, moscow_date, period_start
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import format_rub, input_rub
from pricewatch.domain.purchases import PurchaseRecord, baseline_price, new_purchase, purchase_stats, purchase_stats_by_category
from pricewatch.domain.shopping import Item, ItemState, Purchase
from pricewatch.domain.undo import UndoKind, issue_undo

# Раньше этой даты покупка — опечатка в годе, а не старая покупка.
PURCHASE_DATE_MIN = date(2000, 1, 1)


@dataclass(frozen=True, slots=True)
class PurchaseView:
    """Покупка для показа: цена за штуку, количество, сумма, день и магазин (если указан)."""

    item_id: int
    name: str
    purchased_at: datetime
    purchased_on: date
    price_minor: int
    price_text: str
    quantity: int
    total_minor: int
    total_text: str
    store: StoreView | None


def purchase_view(item_id: int, name: str, purchase: Purchase, stores: dict[int, StoreInfo]) -> PurchaseView:
    """`stores` должны содержать магазин покупки (см. purchase_store_ids)."""
    amount = purchase.price_minor * purchase.quantity
    store = None if purchase.store_id is None else stores[purchase.store_id]
    return PurchaseView(
        item_id=item_id,
        name=name,
        purchased_at=purchase.purchased_at,
        purchased_on=moscow_date(purchase.purchased_at),
        price_minor=purchase.price_minor,
        price_text=format_rub(purchase.price_minor),
        quantity=purchase.quantity,
        total_minor=amount,
        total_text=format_rub(amount),
        store=None if store is None else StoreView(store.store_id, store.name, store.color_token),
    )


def purchase_store_ids(purchases: Iterable[Purchase]) -> list[int]:
    return sorted({purchase.store_id for purchase in purchases if purchase.store_id is not None})


@dataclass(frozen=True, slots=True)
class PurchaseForm:
    """Данные формы «Куплено»; `price_input` пуст, если цены сейчас нет."""

    item_id: int
    version: int
    name: str
    price_input: str
    stores: list[StoreView]
    store_id: int | None
    quantity: int
    today: date


def purchase_form(uow: UnitOfWork, user: User, item_id: int, now: datetime) -> PurchaseForm:
    with uow:
        item = _in_list(uow.items.get(user.id, item_id))
        priced = load(uow, user.id, Period.DAY, now, now, item_ids=frozenset({item_id}))
    row = only_row(priced)
    price = row.now.price_minor if row.now.participates else None
    best = row.now.best if price is not None else None
    stores = {binding.link.store_id: priced.stores[binding.link.store_id] for binding in row.total_item.bindings}
    return PurchaseForm(
        item_id=item.id,
        version=item.version,
        name=item.name,
        price_input="" if price is None else input_rub(price),
        stores=sorted((StoreView(store.store_id, store.name, store.color_token) for store in stores.values()), key=lambda store: store.name),
        store_id=None if best is None else best.store_id,
        quantity=item.quantity,
        today=moscow_date(now),
    )


def purchase_item(
    uow: UnitOfWork, key: bytes, user: User, item_id: int, version: int, *, price_minor: int, quantity: int, store_id: int | None,
    day: date, now: datetime,
) -> str:
    """Токен «Отменить». Магазин — только из ссылок товара; дата — не раньше 2000 года и не позже сегодня."""
    if day < PURCHASE_DATE_MIN:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"purchased_at": "too_early"}})
    purchase = new_purchase(price_minor, quantity, store_id, day_start(day), now)
    with uow:
        item = _in_list(uow.items.get(user.id, item_id))
        total_item = only_row(load(uow, user.id, Period.DAY, now, item.created_at, item_ids=frozenset({item_id}))).total_item
        if store_id is not None and all(binding.link.store_id != store_id for binding in total_item.bindings):
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"purchase_store": "not_item_store"}})
        uow.items.purchase(user.id, item_id, version, purchase, baseline_price(total_item, item.created_at, now))
        uow.commit()
    return issue_undo(key, user.id, UndoKind.PURCHASE, item_id, now)


def archive_item(uow: UnitOfWork, key: bytes, user: User, item_id: int, version: int, now: datetime) -> str:
    with uow:
        uow.items.archive(user.id, item_id, version, now)
        uow.commit()
    return issue_undo(key, user.id, UndoKind.ARCHIVE, item_id, now)


def return_to_list(uow: UnitOfWork, user: User, item_id: int, version: int) -> Item:
    with uow:
        item = uow.items.return_to_list(user.id, item_id, version)
        uow.commit()
    return item


def _in_list(item: Item) -> Item:
    if item.state is not ItemState.ACTIVE:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"state": "invalid_transition"}})
    return item


# --- страница «Покупки» и архив ---


@dataclass(frozen=True, slots=True)
class PurchaseEntry:
    """Покупка в списке страницы; экономия со знаком (минус — переплата), None — цены при добавлении не было."""

    view: PurchaseView
    category_id: int
    category_name: str
    savings_minor: int | None
    savings_text: str | None


@dataclass(frozen=True, slots=True)
class CategorySpending:
    category_id: int
    name: str
    count: int
    spent_minor: int
    spent_text: str
    savings_minor: int | None
    savings_text: str | None


@dataclass(frozen=True, slots=True)
class PurchasesOverview:
    """Покупки за период: «потрачено», экономия, разбивка по категориям (сначала большие траты), список — сначала новые.

    Строки сумм экономии — без знака: знак несёт `savings_minor`.
    """

    period: Period
    count: int
    spent_minor: int
    spent_text: str
    savings_minor: int | None
    savings_text: str | None
    categories: list[CategorySpending]
    entries: list[PurchaseEntry]


@dataclass(frozen=True, slots=True)
class ArchivedEntry:
    """Товар в архиве: строка с последней известной ценой, категория, когда перенесён."""

    row: ItemRowView
    category_id: int
    category_name: str
    archived_at: datetime


def purchases_overview(uow: UnitOfWork, user: User, period: Period, now: datetime) -> PurchasesOverview:
    with uow:
        bought = uow.price_data.purchases(user.id)
        stores = uow.price_data.stores(purchase_store_ids(entry.purchase for entry in bought))
    start = period_start(period, now)
    in_period = [entry for entry in bought if start <= entry.purchase.purchased_at <= now]
    records = [PurchaseRecord(entry.item_id, entry.category_id, entry.purchase, entry.baseline_minor) for entry in in_period]
    stats = purchase_stats(records, period, now)
    names = {entry.category_id: entry.category_name for entry in in_period}
    categories = [
        CategorySpending(category_id, names[category_id], value.count, value.spent_minor, format_rub(value.spent_minor),
                         value.savings_minor, _unsigned(value.savings_minor))
        for category_id, value in purchase_stats_by_category(records, period, now).items()
    ]
    categories.sort(key=lambda spending: (-spending.spent_minor, spending.name, spending.category_id))
    entries = []
    for entry in in_period:
        purchase = entry.purchase
        savings = None if entry.baseline_minor is None else (entry.baseline_minor - purchase.price_minor) * purchase.quantity
        view = purchase_view(entry.item_id, entry.name, purchase, stores)
        entries.append(PurchaseEntry(view, entry.category_id, entry.category_name, savings, _unsigned(savings)))
    return PurchasesOverview(
        period, stats.count, stats.spent_minor, format_rub(stats.spent_minor), stats.savings_minor, _unsigned(stats.savings_minor), categories, entries
    )


def archive_list(uow: UnitOfWork, user: User, now: datetime) -> list[ArchivedEntry]:
    """Архивные товары, сначала недавно перенесённые; цена — последняя известная (domain.pricing)."""
    with uow:
        archived = {item.item_id: item for item in uow.price_data.items(user.id) if item.state is ItemState.ARCHIVED}
        if not archived:
            return []
        priced = load(uow, user.id, Period.DAY, now, now, item_ids=frozenset(archived))
        names = {category.id: category.name for category in uow.categories.list_active(user.id)}
    entries = []
    for row in priced.rows:
        item = archived[row.item.item_id]
        if item.archived_at is None:
            raise ValueError("у архивного товара нет даты архива")
        entries.append(ArchivedEntry(item_row(row, priced.stores), item.category_id, names[item.category_id], item.archived_at))
    entries.sort(key=lambda entry: (entry.archived_at, entry.row.item_id), reverse=True)
    return entries


def _unsigned(amount: int | None) -> str | None:
    return None if amount is None else format_rub(abs(amount))
