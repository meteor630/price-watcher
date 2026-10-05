"""Точки графиков (DEVELOPMENT_ARCHITECTURE.md, разделы 6 и 9).

Одна точка на московский день: цена на конец дня, для сегодняшнего дня — на `now`.
Диапазоны неделя, месяц и год — 7, 30 и 365 точек, последняя — сегодня.

- График магазина — цена за одну штуку по ссылке этого магазина, привязанной
  к товару на конец дня; замена ссылки того же магазина продолжает линию.
  Это история самого магазина, поэтому его выключение точки не прячет.
- График суммы — «сколько стоил бы нынешний список»: сегодняшние количества
  и отметки, цены каждого дня по правилам итога. Выключенный магазин не
  учитывается и в прошлом; после включения его цены возвращаются.

Если ни в одной точке нет значения, график пуст: истории ещё нет.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from pricewatch.domain.days import date_label, day_start, moscow_date
from pricewatch.domain.money import format_rub
from pricewatch.domain.pricing import STALE_AFTER, LinkInput, PriceStatus, link_price
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


def store_chart(
    item: TotalItem, store_id: int, chart_range: ChartRange, now: datetime, stale_after: timedelta = STALE_AFTER
) -> list[ChartPoint]:
    points = []
    for day, moment in chart_days(chart_range, now):
        link = next((b.link for b in item.bindings if b.link.store_id == store_id and b.active_at(moment)), None)
        points.append(_store_point(day, link, moment, stale_after))
    return _or_empty(points)


def sum_chart(
    items: Sequence[TotalItem], chart_range: ChartRange, now: datetime, stale_after: timedelta = STALE_AFTER
) -> list[ChartPoint]:
    points = []
    for day, moment in chart_days(chart_range, now):
        value = 0
        counted = stale = False
        for item in items:
            price = item_price_at(item, moment, stale_after)
            unit = counted_unit_price(item, price)
            if unit is not None:
                value += unit * item.quantity
                counted = True
            elif price.status is PriceStatus.STALE:
                stale = True
        if counted:
            points.append(ChartPoint(day, value, f"{date_label(day)} · {format_rub(value)}", PointStatus.OK))
        elif stale:
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
