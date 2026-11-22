"""Главная (SITE_DESIGN_SPEC.md, раздел 4; схема Dashboard в openapi/v1.yaml).

- Итог и изменение за период — по правилам итога (domain.totals): только товары «в списке»,
  изменение «только из-за цен».
- Карточка категории: итог, изменение, график суммы за 30 дней, самый изменившийся товар.
  Если ни у одного товара цена не изменилась, самого изменившегося нет.
- Сводные панели: товары «в списке» со ссылками; подешевевшие и подорожавшие за период
  (цена за штуку учтена в обоих моментах); товары, которых нет в наличии.
- Списки панелей сортируются по величине изменения в процентах, затем по рублям и номеру товара.
- Проверка: последний плановый или ручной запуск; запустить и остановить может только администратор.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from pricewatch.application.ports import StoreInfo, UnitOfWork
from pricewatch.application.price_view import PricedRow, StoreView, load, price_text, store_view
from pricewatch.application.reviews import cards_in
from pricewatch.application.worker_protocol import LEASE
from pricewatch.domain.accounts import User
from pricewatch.domain.charts import ChartPoint, ChartRange, chart_days, sum_chart
from pricewatch.domain.days import Period, day_start, moscow_date
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import Change, Direction, format_rub, percent_tenths
from pricewatch.domain.pricing import PriceStatus
from pricewatch.domain.totals import in_total_shown, total, total_change

SPARKLINE_RANGE = ChartRange.MONTH
PANEL_LIMIT_MAX = 100
ACTIVE_RUN = frozenset({"queued", "running", "stopping"})
# Worker молчит дольше аренды задания — его задания уже возвращаются в очередь, проверка стоит.
WORKER_SILENT_AFTER = LEASE


class Panel(StrEnum):
    TRACKED = "tracked"
    CHEAPER = "cheaper"
    PRICIER = "pricier"
    OUT_OF_STOCK = "out_of_stock"


@dataclass(frozen=True, slots=True)
class TopMover:
    item_id: int
    name: str
    percent_text: str
    direction: Direction


@dataclass(frozen=True, slots=True)
class CategoryCard:
    category_id: int
    name: str
    version: int
    in_total_count: int
    watching_count: int
    total_minor: int
    total_text: str
    change: Change | None
    sparkline: list[ChartPoint]
    top_mover: TopMover | None


@dataclass(frozen=True, slots=True)
class Panels:
    tracked: int
    cheaper: int
    pricier: int
    out_of_stock: int


@dataclass(frozen=True, slots=True)
class RunView:
    run_id: int | None
    kind: str | None
    status: str
    total: int
    done: int
    failed: int
    started_at: datetime | None
    finished_at: datetime | None
    can_start: bool
    can_stop: bool
    # Сколько шла или идёт проверка; None — проверок не было.
    elapsed: timedelta | None


@dataclass(frozen=True, slots=True)
class Dashboard:
    as_of: date
    period: Period
    total_minor: int
    total_text: str
    change: Change | None
    in_total_count: int
    watching_count: int
    categories: list[CategoryCard]
    categories_order_version: int
    panels: Panels
    run: RunView
    # Сколько карточек «Требует решения» у пользователя (каждый решает за свои товары).
    reviews_pending: int


@dataclass(frozen=True, slots=True)
class ItemChangeView:
    item_id: int
    name: str
    store: StoreView | None
    category_id: int
    category_name: str
    old_price_text: str | None
    new_price_text: str | None
    change: Change | None


@dataclass(frozen=True, slots=True)
class PanelPage:
    items: list[ItemChangeView]
    next_offset: int | None


def dashboard(uow: UnitOfWork, user: User, period: Period, now: datetime) -> Dashboard:
    with uow:
        categories = uow.categories.list_active(user.id)
        order_version = uow.categories.order_version(user.id)
        rows, stores = _rows(uow, user.id, period, now)
        run = _run_view(uow, user, now)
        reviews = len(cards_in(uow, user, now))
    totals = [row.total_item for row in rows]
    cards = []
    for category in categories:
        own = [row for row in rows if row.item.category_id == category.id]
        own_totals = [row.total_item for row in own]
        amount = total(own_totals, now)
        cards.append(
            CategoryCard(
                category.id, category.name, category.version,
                in_total_count=sum(1 for row in own if in_total_shown(row.total_item, row.now)),
                watching_count=sum(1 for row in own if not in_total_shown(row.total_item, row.now)),
                total_minor=amount,
                total_text=format_rub(amount),
                change=total_change(own_totals, period, now),
                sparkline=sum_chart(own_totals, SPARKLINE_RANGE, now),
                top_mover=_top_mover(own),
            )
        )
    amount = total(totals, now)
    return Dashboard(
        as_of=moscow_date(now),
        period=period,
        total_minor=amount,
        total_text=format_rub(amount),
        change=total_change(totals, period, now),
        in_total_count=sum(1 for row in rows if in_total_shown(row.total_item, row.now)),
        watching_count=sum(1 for row in rows if not in_total_shown(row.total_item, row.now)),
        categories=cards,
        categories_order_version=order_version,
        panels=Panels(*(len(_panel_rows(rows, panel)) for panel in Panel)),
        run=run,
        reviews_pending=reviews,
    )


def dashboard_chart(uow: UnitOfWork, user: User, chart_range: ChartRange, now: datetime) -> list[ChartPoint]:
    first_day, _ = chart_days(chart_range, now)[0]
    with uow:
        priced = load(uow, user.id, Period.DAY, now, day_start(first_day))
    return sum_chart([row.total_item for row in priced.rows], chart_range, now)


def panel_items(uow: UnitOfWork, user: User, panel: Panel, period: Period, now: datetime, offset: int, limit: int) -> PanelPage:
    if offset < 0 or not 1 <= limit <= PANEL_LIMIT_MAX:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"cursor" if offset < 0 else "limit": "invalid"}})
    with uow:
        names = {category.id: category.name for category in uow.categories.list_active(user.id)}
        rows, stores = _rows(uow, user.id, period, now)
    chosen = _panel_rows(rows, panel)
    page = chosen[offset : offset + limit]
    views = [
        ItemChangeView(
            row.item.item_id,
            row.item.name,
            store_view(stores, row.now.best),
            row.item.category_id,
            names[row.item.category_id],
            price_text(row.before.price_minor) if row.change is not None else None,
            price_text(row.now.price_minor),
            row.change,
        )
        for row in page
    ]
    following = offset + limit
    return PanelPage(views, following if following < len(chosen) else None)


def _rows(uow: UnitOfWork, user_id: int, period: Period, now: datetime) -> tuple[list[PricedRow], dict[int, StoreInfo]]:
    """Товары «в списке» с ценами сейчас и в начале периода; наблюдения — с начала графика за 30 дней."""
    first_day, _ = chart_days(SPARKLINE_RANGE, now)[0]
    priced = load(uow, user_id, period, now, day_start(first_day))
    return priced.rows, priced.stores


def _panel_rows(rows: Sequence[PricedRow], panel: Panel) -> list[PricedRow]:
    if panel is Panel.TRACKED:
        chosen = [row for row in rows if row.now.links]
    elif panel is Panel.CHEAPER:
        chosen = [row for row in rows if row.change is not None and row.change.direction is Direction.DOWN]
    elif panel is Panel.PRICIER:
        chosen = [row for row in rows if row.change is not None and row.change.direction is Direction.UP]
    else:
        chosen = [row for row in rows if row.now.status is PriceStatus.OUT_OF_STOCK]
    return sorted(chosen, key=lambda row: (*_magnitude(row), row.item.item_id))


def _magnitude(row: PricedRow) -> tuple[int, int]:
    """Ключ сортировки: сначала большее изменение в процентах, затем в рублях; без изменения — в конце."""
    if row.change is None or row.before.price_minor is None:
        return (1, 0)
    return (-percent_tenths(row.change.change_minor, row.before.price_minor), -abs(row.change.change_minor))


def _top_mover(rows: Sequence[PricedRow]) -> TopMover | None:
    changed = [(row, row.change) for row in rows if row.change is not None and row.change.change_minor != 0]
    if not changed:
        return None
    best, change = min(changed, key=lambda pair: (*_magnitude(pair[0]), pair[0].item.item_id))
    return TopMover(best.item.item_id, best.item.name, change.percent_text, change.direction)


def run_status(uow: UnitOfWork, user: User, now: datetime) -> RunView:
    """Статус последней проверки для шапки любой страницы и блока запуска на главной."""
    with uow:
        return _run_view(uow, user, now)


def _run_view(uow: UnitOfWork, user: User, now: datetime) -> RunView:
    """Идущая проверка, к которой worker не обращался дольше аренды, показывается как worker_offline."""
    latest = uow.queue.latest_run()
    if latest is None:
        return RunView(None, None, "idle", 0, 0, 0, None, None, can_start=user.is_admin, can_stop=False, elapsed=None)
    progress = latest.progress
    active = progress.status in ACTIVE_RUN
    status = progress.status
    if progress.status in {"queued", "running"}:
        seen = uow.workers.last_seen()
        if seen is None or now - seen >= WORKER_SILENT_AFTER:
            status = "worker_offline"
    return RunView(
        progress.run_id, progress.kind, status, progress.total, progress.done, progress.failed,
        latest.started_at, latest.finished_at,
        can_start=user.is_admin and not active,
        can_stop=user.is_admin and progress.status in {"queued", "running"},
        elapsed=None if latest.started_at is None else (latest.finished_at or now) - latest.started_at,
    )

