"""Точки графиков (DEVELOPMENT_ARCHITECTURE.md, разделы 6 и 9).

Одна точка на московский день: цена на конец дня, для сегодняшнего дня — на `now`.
Диапазоны неделя, месяц и год — 7, 30 и 365 точек, последняя — сегодня.

- График магазина — цена за одну штуку по ссылке этого магазина, привязанной
  к товару на конец дня; замена ссылки того же магазина продолжает линию.
  Это история самого магазина, поэтому его выключение точки не прячет.
- График суммы — «сколько стоил бы нынешний список»: сегодняшние количества
  и отметки. В корзину сравнения входят товары с ценой хотя бы за один день
  диапазона. Сумма появляется только за дни, когда цены есть у всей корзины.

Если ни в одной точке нет значения, график пуст: истории ещё нет.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from pricewatch.domain.days import date_label, day_start, moscow_date
from pricewatch.domain.money import format_rub
from pricewatch.domain.pricing import LinkInput, PriceStatus, link_price
from pricewatch.domain.shopping import ItemState
from pricewatch.domain.totals import BEFORE, TotalItem, counted_unit_price, item_price_at


class ChartRange(StrEnum):
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


RANGE_DAYS = {ChartRange.WEEK: 7, ChartRange.MONTH: 30, ChartRange.YEAR: 365}


class PointStatus(StrEnum):
    OK = "ok"
    NO_DATA = "no_data"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class ChartPoint:
    day: date
    value_minor: int | None
    label: str
    status: PointStatus


def chart_days(chart_range: ChartRange, now: datetime) -> list[tuple[date, datetime]]:
    """Дни диапазона по возрастанию и момент, на который берётся цена каждого дня."""
    today = moscow_date(now)
    days = [today - timedelta(days=back) for back in range(RANGE_DAYS[chart_range] - 1, -1, -1)]
    return [(day, now if day == today else day_start(day + timedelta(days=1)) - BEFORE) for day in days]


def store_chart(item: TotalItem, store_id: int, chart_range: ChartRange, now: datetime) -> list[ChartPoint]:
    points = []
    for day, moment in chart_days(chart_range, now):
        link = next((b.link for b in item.bindings if b.link.store_id == store_id and b.active_at(moment)), None)
        points.append(_store_point(day, link, moment, item.stale_after))
    return _or_empty(points)


def sum_chart(items: Sequence[TotalItem], chart_range: ChartRange, now: datetime) -> list[ChartPoint]:
    days = chart_days(chart_range, now)
    sums = [0] * len(days)
    known = [0] * len(days)
    stale = [False] * len(days)
    basket_size = 0
    for item in items:
        if item.state is not ItemState.ACTIVE or not item.in_total:
            continue
        prices = [item_price_at(item, moment) for _, moment in days]
        units = [counted_unit_price(item, price) for price in prices]
        if all(unit is None for unit in units):
            continue
        basket_size += 1
        for index, unit in enumerate(units):
            if unit is not None:
                sums[index] += unit * item.quantity
                known[index] += 1
            elif prices[index].status is PriceStatus.STALE:
                stale[index] = True

    points = []
    for index, (day, _) in enumerate(days):
        if basket_size and known[index] == basket_size:
            value = sums[index]
            points.append(ChartPoint(day, value, f"{date_label(day)} · {format_rub(value)}", PointStatus.OK))
        elif stale[index]:
            points.append(ChartPoint(day, None, f"{date_label(day)} · цена не актуальна", PointStatus.STALE))
        else:
            points.append(_no_data(day))
    return _or_empty(points)


def _store_point(day: date, link: LinkInput | None, moment: datetime, stale_after: timedelta) -> ChartPoint:
    if link is None:
        return _no_data(day)
    # История магазина видна и после его выключения: считаем цену как у включённого.
    price = link_price(LinkInput(link.link_id, link.store_id, True, link.observations), moment, stale_after)
    if price.participates and price.price_minor is not None:
        return ChartPoint(day, price.price_minor, f"{date_label(day)} · {format_rub(price.price_minor)}", PointStatus.OK)
    if price.status is PriceStatus.STALE:
        return ChartPoint(day, None, f"{date_label(day)} · цена не актуальна", PointStatus.STALE)
    if price.status is PriceStatus.OUT_OF_STOCK:
        return ChartPoint(day, None, f"{date_label(day)} · нет в наличии", PointStatus.NO_DATA)
    return _no_data(day)


def _no_data(day: date) -> ChartPoint:
    return ChartPoint(day, None, f"{date_label(day)} · нет данных", PointStatus.NO_DATA)


def _or_empty(points: list[ChartPoint]) -> list[ChartPoint]:
    return points if any(point.value_minor is not None for point in points) else []
