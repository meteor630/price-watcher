"""Покупка и архив товара (DEVELOPMENT_ARCHITECTURE.md, раздел 6; SITE_DESIGN_SPEC.md, раздел 6).

- «Куплено»: цена за штуку (по умолчанию — лучшая сейчас), количество (по умолчанию — товара), магазин из ссылок
  товара (в том числе прежних) или без магазина, дата не позже сегодня. Момент покупки — 00:00 по Москве
  выбранного дня: с ним совпадают границы периодов статистики, 14 суток проверки считаются от дня покупки.
- При покупке запоминается лучшая цена в момент добавления товара (domain.purchases.baseline_price): экономия
  не пересчитывается задним числом.
- «В архив» и «Вернуть в список» — по версии записи. Покупку и архив можно отменить токеном (15 секунд).
"""

from dataclasses import dataclass
from datetime import date, datetime

from pricewatch.application.ports import StoreInfo, UnitOfWork
from pricewatch.application.price_view import StoreView, load, only_row
from pricewatch.domain.accounts import User
from pricewatch.domain.days import Period, day_start, moscow_date
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import format_rub, input_rub
from pricewatch.domain.purchases import baseline_price, new_purchase
from pricewatch.domain.shopping import Item, ItemState
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


def purchase_view(item: Item, stores: dict[int, StoreInfo]) -> PurchaseView:
    """`stores` должны содержать магазин покупки (см. purchase_store_ids)."""
    purchase = item.purchase
    if purchase is None:
        raise ValueError("товар не куплен")
    amount = purchase.price_minor * purchase.quantity
    store = None if purchase.store_id is None else stores[purchase.store_id]
    return PurchaseView(
        item_id=item.id,
        name=item.name,
        purchased_at=purchase.purchased_at,
        purchased_on=moscow_date(purchase.purchased_at),
        price_minor=purchase.price_minor,
        price_text=format_rub(purchase.price_minor),
        quantity=purchase.quantity,
        total_minor=amount,
        total_text=format_rub(amount),
        store=None if store is None else StoreView(store.store_id, store.name, store.color_token),
    )


def purchase_store_ids(items: list[Item]) -> list[int]:
    return sorted({item.purchase.store_id for item in items if item.purchase is not None and item.purchase.store_id is not None})


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
