"""Карточка товара и её изменения (SITE_DESIGN_SPEC.md, раздел 6; схемы ItemDetail, StoreOffer, HistoryEvent).

- Цены по магазинам — по ссылкам, привязанным сейчас; лучшая отмечена. Причина сбоя — если цена показана
  после неудачной проверки. Фото — сохранённое на своём домене, ссылок на сервисы магазинов нет.
- История — события всех ссылок товара за год (в том числе заменённых), сначала новые, не больше 30.
- График — цена за штуку по одному магазину; количество на него не влияет. Магазин не из ссылок товара — NOT_FOUND.
- Купленный товар: цена, количество, день и магазин покупки; 14 суток после покупки — «сейчас дешевле на …»
  (разница за штуку с актуальной ценой). Архивный — момент переноса в архив.
- Изменение количества и отметки «в итоге» — с версией записи (чужая правка — VERSION_CONFLICT).
  Отметка у товара с неактуальной ценой включает и «устаревшую цену в итоге»: последняя цена снова учитывается,
  предупреждение остаётся. Снятая отметка снимает и этот режим.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.application.ports import UNCHANGED, ItemChanges, LinkInfo, StoreInfo, Unchanged, UnitOfWork
from pricewatch.application.price_view import PricedRow, StoreView, item_row, load, only_row, price_text
from pricewatch.application.purchases import PurchaseView, purchase_store_ids, purchase_view
from pricewatch.domain.accounts import User
from pricewatch.domain.charts import ChartPoint, ChartRange, chart_days, store_chart
from pricewatch.domain.days import Period, day_start
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.history import EventKind, link_history
from pricewatch.domain.money import format_rub
from pricewatch.domain.pricing import PriceStatus
from pricewatch.domain.purchases import cheaper_now
from pricewatch.domain.shopping import Item, ItemState

HISTORY_WINDOW = timedelta(days=365)
HISTORY_LIMIT = 30
MEDIA_PREFIX = "/media/"


@dataclass(frozen=True, slots=True)
class Offer:
    store: StoreView
    url: str
    price_status: PriceStatus
    availability: str
    price_minor: int | None
    price_text: str | None
    card_price_minor: int | None
    card_price_text: str | None
    checked_at: datetime | None
    is_best: bool


@dataclass(frozen=True, slots=True)
class Event:
    at: datetime
    store: StoreView
    kind: EventKind
    text: str


@dataclass(frozen=True, slots=True)
class ItemDetail:
    item_id: int
    version: int
    name: str
    full_name: str | None
    state: ItemState
    category_id: int
    category_name: str
    group_id: int | None
    group_name: str | None
    quantity: int
    in_total: bool
    include_stale: bool
    photo_url: str | None
    price_status: PriceStatus
    under_review: bool
    best_store: StoreView | None
    unit_price_minor: int | None
    unit_price_text: str | None
    line_price_text: str | None
    regular_price_text: str | None
    failure_reason: str | None
    offers: list[Offer]
    history: list[Event]
    # Магазины, по которым есть график: ссылки товара, в том числе прежние.
    chart_stores: list[StoreView]
    purchase: PurchaseView | None
    # На сколько за штуку товар сейчас дешевле цены покупки (только 14 суток после покупки).
    cheaper_now_minor: int | None
    cheaper_now_text: str | None
    archived_at: datetime | None
    # Срок актуальности цены из настроек — для пояснения «не обновлялась больше N суток».
    stale_after_days: int


def item_detail(uow: UnitOfWork, user: User, item_id: int, now: datetime) -> ItemDetail:
    with uow:
        return _detail(uow, user, item_id, now)


def item_chart(uow: UnitOfWork, user: User, item_id: int, store_id: int, chart_range: ChartRange, now: datetime) -> list[ChartPoint]:
    first_day, _ = chart_days(chart_range, now)[0]
    with uow:
        uow.items.get(user.id, item_id)
        priced = load(uow, user.id, Period.DAY, now, day_start(first_day), item_ids=frozenset({item_id}))
    total_item = only_row(priced).total_item
    if all(binding.link.store_id != store_id for binding in total_item.bindings):
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return store_chart(total_item, store_id, chart_range, now)


def update_item_settings(
    uow: UnitOfWork, user: User, item_id: int, version: int, now: datetime,
    *, quantity: int | Unchanged = UNCHANGED, in_total: bool | Unchanged = UNCHANGED,
) -> ItemDetail:
    if quantity is UNCHANGED and in_total is UNCHANGED:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"body": "nothing_to_change"}})
    with uow:
        include_stale: bool | Unchanged = UNCHANGED
        uow.items.get(user.id, item_id)
        if in_total is not UNCHANGED:
            priced = load(uow, user.id, Period.DAY, now, now, item_ids=frozenset({item_id}))
            include_stale = bool(in_total) and only_row(priced).now.status is PriceStatus.STALE
        uow.items.update(user.id, item_id, version, ItemChanges(quantity=quantity, in_total=in_total, include_stale=include_stale))
        uow.commit()
        return _detail(uow, user, item_id, now)


def _detail(uow: UnitOfWork, user: User, item_id: int, now: datetime) -> ItemDetail:
    item = uow.items.get(user.id, item_id)
    category = uow.categories.get(user.id, item.category_id)
    group = uow.groups.get(user.id, item.group_id) if item.group_id is not None else None
    priced = load(uow, user.id, Period.DAY, now, now - HISTORY_WINDOW, item_ids=frozenset({item_id}))
    row = only_row(priced)
    links = uow.price_data.links(sorted({binding.link.link_id for binding in row.total_item.bindings}))
    purchase = None if item.purchase is None else purchase_view(item.id, item.name, item.purchase, uow.price_data.stores(purchase_store_ids([item.purchase])))
    return _assemble(row, priced.stores, links, category.name, group.name if group else None, item, purchase, now)


def _store(stores: dict[int, StoreInfo], store_id: int) -> StoreView:
    return StoreView(store_id, stores[store_id].name, stores[store_id].color_token)


def _assemble(
    row: PricedRow, stores: dict[int, StoreInfo], links: dict[int, LinkInfo], category_name: str, group_name: str | None,
    item: Item, purchase: PurchaseView | None, now: datetime,
) -> ItemDetail:
    view = item_row(row, stores)
    price = row.now
    best_id = price.best.link_id if price.best is not None else None
    observations = {binding.link.link_id: binding.link.observations for binding in row.total_item.bindings}
    offers = []
    for link in price.links:
        seen = [observation for observation in observations[link.link_id] if observation.checked_at <= now]
        last = link.observation
        offers.append(
            Offer(
                store=_store(stores, link.store_id),
                url=links[link.link_id].url,
                price_status=link.status,
                availability=last.availability.value if last is not None else "unknown",
                price_minor=None if last is None else last.price_minor,
                price_text=None if last is None else price_text(last.price_minor),
                card_price_minor=None if last is None else last.card_price_minor,
                card_price_text=None if last is None else price_text(last.card_price_minor),
                checked_at=seen[-1].checked_at if seen else None,
                is_best=link.link_id == best_id,
            )
        )
    events = []
    for binding in row.total_item.bindings:
        store = _store(stores, binding.link.store_id)
        for event in link_history(binding.link.observations):
            if binding.active_at(event.at) and event.at <= now:
                events.append(Event(event.at, store, event.kind, event.text))
    events.sort(key=lambda event: event.at, reverse=True)
    photo = next((links[link_id].image_path for link_id in ([best_id] if best_id else []) + sorted(links) if links[link_id].image_path), None)
    failure = price.best.failure if price.best is not None else None
    chart_stores = {binding.link.store_id: _store(stores, binding.link.store_id) for binding in row.total_item.bindings}
    cheaper = None if item.purchase is None else cheaper_now(item.purchase, price, now)
    return ItemDetail(
        item_id=view.item_id,
        version=view.version,
        name=view.name,
        full_name=item.full_name,
        state=row.item.state,
        category_id=row.item.category_id,
        category_name=category_name,
        group_id=row.item.group_id,
        group_name=group_name,
        quantity=view.quantity,
        in_total=view.in_total,
        include_stale=row.item.include_stale,
        photo_url=None if photo is None else MEDIA_PREFIX + photo,
        price_status=view.price_status,
        under_review=view.under_review,
        best_store=view.best_store,
        unit_price_minor=view.unit_price_minor,
        unit_price_text=view.unit_price_text,
        line_price_text=view.line_price_text,
        regular_price_text=view.regular_price_text,
        failure_reason=None if failure is None else SAFE_MESSAGES[failure],
        offers=offers,
        history=events[:HISTORY_LIMIT],
        chart_stores=list(chart_stores.values()),
        purchase=purchase,
        cheaper_now_minor=cheaper,
        cheaper_now_text=None if cheaper is None else format_rub(cheaper),
        archived_at=item.archived_at,
        stale_after_days=row.total_item.stale_after.days,
    )
