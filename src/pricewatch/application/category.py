"""Страницы категории и группы (SITE_DESIGN_SPEC.md, разделы 5 и 7; схема CategoryDetail в openapi/v1.yaml).

- Категория: итог и изменение за период, товары «в списке» блоками — сначала без группы, затем группы
  в их порядке, пустые группы тоже видны; у блока своя сумма по правилам итога.
- График категории — «сколько стоил бы нынешний список» по дням (domain.charts.sum_chart).
- Группа: у товара цены всех магазинов, привязанных сейчас, лучшая отмечена; «минимум периода» —
  самая низкая подтверждённая цена за штуку за период с датой и магазином (domain.totals.period_minimum).
- Чужая или удалённая категория и группа — NOT_FOUND (проверку владельца делает репозиторий).
"""

from dataclasses import dataclass
from datetime import date, datetime

from pricewatch.application.ports import StoreInfo, UnitOfWork
from pricewatch.application.price_view import ItemRowView, PricedRow, StoreView, item_row, load, price_text
from pricewatch.domain.accounts import User
from pricewatch.domain.charts import ChartPoint, ChartRange, chart_days, sum_chart
from pricewatch.domain.days import Period, day_start, moscow_date, period_start
from pricewatch.domain.money import Change, format_rub
from pricewatch.domain.pricing import PriceStatus
from pricewatch.domain.totals import in_total_shown, period_minimum, total, total_change


@dataclass(frozen=True, slots=True)
class GroupBlock:
    group_id: int | None
    name: str | None
    version: int | None
    items: list[ItemRowView]
    total_minor: int
    total_text: str


@dataclass(frozen=True, slots=True)
class CategoryDetail:
    category_id: int
    name: str
    version: int
    period: Period
    total_minor: int
    total_text: str
    change: Change | None
    in_total_count: int
    watching_count: int
    groups: list[GroupBlock]


@dataclass(frozen=True, slots=True)
class StoreOffer:
    store: StoreView
    price_text: str | None
    status: PriceStatus
    best: bool


@dataclass(frozen=True, slots=True)
class PeriodLow:
    price_text: str
    day: date
    store_name: str


@dataclass(frozen=True, slots=True)
class GroupItem:
    row: ItemRowView
    offers: list[StoreOffer]
    minimum: PeriodLow | None


@dataclass(frozen=True, slots=True)
class GroupDetail:
    group_id: int
    name: str
    category_id: int
    category_name: str
    period: Period
    items: list[GroupItem]


def category_detail(uow: UnitOfWork, user: User, category_id: int, period: Period, now: datetime) -> CategoryDetail:
    with uow:
        category = uow.categories.get(user.id, category_id)
        groups = uow.groups.list_in_category(user.id, category_id)
        priced = load(uow, user.id, period, now, period_start(period, now))
    rows = [row for row in priced.rows if row.item.category_id == category_id]
    blocks = []
    loose = [row for row in rows if row.item.group_id is None]
    if loose:
        blocks.append(_block(None, None, None, loose, priced.stores, now))
    for group in groups:
        blocks.append(_block(group.id, group.name, group.version, [row for row in rows if row.item.group_id == group.id], priced.stores, now))
    totals = [row.total_item for row in rows]
    amount = total(totals, now)
    return CategoryDetail(
        category_id=category.id,
        name=category.name,
        version=category.version,
        period=period,
        total_minor=amount,
        total_text=format_rub(amount),
        change=total_change(totals, period, now),
        in_total_count=sum(1 for row in rows if in_total_shown(row.total_item, row.now)),
        watching_count=sum(1 for row in rows if not in_total_shown(row.total_item, row.now)),
        groups=blocks,
    )


def category_chart(uow: UnitOfWork, user: User, category_id: int, chart_range: ChartRange, now: datetime) -> list[ChartPoint]:
    first_day, _ = chart_days(chart_range, now)[0]
    with uow:
        uow.categories.get(user.id, category_id)
        priced = load(uow, user.id, Period.DAY, now, day_start(first_day))
    return sum_chart([row.total_item for row in priced.rows if row.item.category_id == category_id], chart_range, now)


def group_detail(uow: UnitOfWork, user: User, group_id: int, period: Period, now: datetime) -> GroupDetail:
    with uow:
        group = uow.groups.get(user.id, group_id)
        category = uow.categories.get(user.id, group.category_id)
        priced = load(uow, user.id, period, now, period_start(period, now))
    items = []
    for row in priced.rows:
        if row.item.group_id != group_id:
            continue
        best_link = row.now.best.link_id if row.now.best is not None else None
        offers = [
            StoreOffer(
                StoreView(link.store_id, priced.stores[link.store_id].name, priced.stores[link.store_id].color_token),
                price_text(link.price_minor),
                link.status,
                best=link.link_id == best_link,
            )
            for link in row.now.links
        ]
        low = period_minimum(row.total_item, period, now)
        minimum = None if low is None else PeriodLow(format_rub(low.price_minor), moscow_date(low.checked_at), priced.stores[low.store_id].name)
        items.append(GroupItem(item_row(row, priced.stores), offers, minimum))
    return GroupDetail(group.id, group.name, category.id, category.name, period, items)


def _block(group_id: int | None, name: str | None, version: int | None, rows: list[PricedRow], stores: dict[int, StoreInfo], now: datetime) -> GroupBlock:
    amount = total([row.total_item for row in rows], now)
    return GroupBlock(group_id, name, version, [item_row(row, stores) for row in rows], amount, format_rub(amount))


@dataclass(frozen=True, slots=True)
class GroupOverview:
    group_id: int
    name: str
    version: int
    item_count: int


@dataclass(frozen=True, slots=True)
class CategoryOverview:
    category_id: int
    name: str
    version: int
    item_count: int
    total_text: str
    groups: list[GroupOverview]


@dataclass(frozen=True, slots=True)
class CategoriesOverview:
    order_version: int
    categories: list[CategoryOverview]


def categories_overview(uow: UnitOfWork, user: User, now: datetime) -> CategoriesOverview:
    """Настройки «Категории»: порядок, число товаров «в списке», итог и группы каждой категории."""
    with uow:
        categories = uow.categories.list_active(user.id)
        order_version = uow.categories.order_version(user.id)
        groups = {category.id: uow.groups.list_in_category(user.id, category.id) for category in categories}
        priced = load(uow, user.id, Period.DAY, now, now)
    overview = []
    for category in categories:
        rows = [row for row in priced.rows if row.item.category_id == category.id]
        overview.append(
            CategoryOverview(
                category.id, category.name, category.version, len(rows), format_rub(total([row.total_item for row in rows], now)),
                [GroupOverview(group.id, group.name, group.version, sum(1 for row in rows if row.item.group_id == group.id)) for group in groups[category.id]],
            )
        )
    return CategoriesOverview(order_version, overview)
