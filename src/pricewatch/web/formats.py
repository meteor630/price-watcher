"""Подписи для показа на сайте: даты по-русски, периоды, статус проверки. Деньги форматирует domain.money."""

from datetime import date, datetime

from pricewatch.domain.days import MOSCOW, Period

MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
PERIOD_NAMES = {Period.DAY: "Сутки", Period.WEEK: "Неделя", Period.MONTH: "Месяц", Period.YEAR: "Год"}
PERIOD_SINCE = {Period.DAY: "за сутки", Period.WEEK: "за неделю", Period.MONTH: "за месяц", Period.YEAR: "за год"}
ARROWS = {"down": "▼", "up": "▲", "flat": "●"}
# Почему у товара такая цена (PriceStatus); у «ok» подписи нет.
STATUS_LABELS = {
    "check_failed": "не удалось проверить",
    "stale": "цена не актуальна",
    "out_of_stock": "нет в наличии",
    "awaiting_check": "ожидает проверки",
    "store_disabled": "магазин отключён",
    "no_links": "нет ссылок на магазины",
}
CHART_RANGE_NAMES = {"week": "Неделя", "month": "Месяц", "year": "Год"}


def period_index(period: Period) -> int:
    """Номер периода в переключателе: по нему CSS ставит подложку активного варианта."""
    return list(PERIOD_NAMES).index(period)


def long_date(day: date) -> str:
    """«6 октября 2026»."""
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year}"


def moscow_moment(moment: datetime) -> str:
    """«06.10 в 10:45» по Москве."""
    local = moment.astimezone(MOSCOW)
    return f"{local:%d.%m} в {local:%H:%M}"


def run_line(status: str, done: int, failed: int, total: int, finished_at: datetime | None) -> tuple[str, str]:
    """Текст статуса проверки в шапке и вид точки: ok, busy, bad."""
    if status in ("queued", "running"):
        return f"Идёт проверка: {done + failed} из {total}", "busy"
    if status == "stopping":
        return f"Проверка останавливается: {done + failed} из {total}", "busy"
    if status == "failed":
        return "Проверка не удалась", "bad"
    if status == "finished" and finished_at is not None:
        return f"Парсер: {moscow_moment(finished_at)}", "ok"
    return "Проверок ещё не было", "idle"
