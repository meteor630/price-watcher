"""Сутки по Москве и начала периодов.

Сутки начинаются в 00:00 по Москве (DEVELOPMENT_ARCHITECTURE.md, раздел 6).
Начало периода — 00:00 сегодня для суток и 00:00 семь, тридцать и триста
шестьдесят пять суток назад для недели, месяца и года. Часовой пояс берётся
из базы IANA (`zoneinfo`, данные — пакет tzdata), а не фиксированным +3:
правила для России менялись в 2011 и 2014 годах. Все моменты — с часовым
поясом; наивное время отклоняется, чтобы не спутать UTC с местным.
"""

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
MONTH_LABELS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


class Period(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


PERIOD_DAYS = {Period.DAY: 0, Period.WEEK: 7, Period.MONTH: 30, Period.YEAR: 365}


def moscow_date(moment: datetime) -> date:
    """Московская дата момента."""
    return _require_aware(moment).astimezone(MOSCOW).date()


def day_start(day: date) -> datetime:
    """00:00 по Москве указанного дня, в UTC."""
    return datetime.combine(day, time(), MOSCOW).astimezone(UTC)


def period_start(period: Period, now: datetime) -> datetime:
    """Начало периода, к которому сравнивается текущая цена, в UTC."""
    return day_start(moscow_date(now) - timedelta(days=PERIOD_DAYS[period]))


def date_label(day: date) -> str:
    """Подпись даты на графиках и в списках: «5 окт»."""
    return f"{day.day} {MONTH_LABELS[day.month - 1]}"


def _require_aware(moment: datetime) -> datetime:
    if moment.utcoffset() is None:
        raise ValueError("время без часового пояса")
    return moment
