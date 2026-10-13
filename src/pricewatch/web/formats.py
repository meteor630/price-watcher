"""Подписи для показа на сайте: даты по-русски, периоды, статус проверки. Деньги форматирует domain.money."""

from datetime import date, datetime, timedelta

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
# Состояние ссылки в окне товара (SITE_DESIGN_SPEC.md, раздел 8).
LINK_STATUS_LABELS = {
    "unchecked": "не проверена",
    "checking": "проверяется…",
    "found": "цена найдена",
    "waiting": "ожидает плановой проверки",
    "not_found": "не удалось найти цену",
    "store_disabled": "Этот сайт отключён администратором",
}


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


REVIEW_KIND_LABELS = {"price_jump": "Скачок цены", "title_changed": "Название изменилось"}

# Проверка ещё идёт: статус в шапке и блок запуска опрашивают сервер, пока она не закончится.
ACTIVE_RUN_STATUSES = frozenset({"queued", "running", "stopping", "worker_offline"})


def duration_text(elapsed: timedelta) -> str:
    """«меньше минуты», «12 мин», «1 ч 5 мин», «3 ч»."""
    minutes = int(elapsed.total_seconds() // 60)
    if minutes < 1:
        return "меньше минуты"
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} мин"
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


def run_line(status: str, done: int, failed: int, total: int, finished_at: datetime | None) -> tuple[str, str]:
    """Текст статуса проверки в шапке и вид точки: ok, busy, bad."""
    if status in ("queued", "running"):
        return f"Идёт проверка: {done + failed} из {total}", "busy"
    if status == "worker_offline":
        return f"Worker не на связи: {done + failed} из {total}", "bad"
    if status == "stopping":
        return f"Проверка останавливается: {done + failed} из {total}", "busy"
    if status == "failed":
        return "Проверка не удалась", "bad"
    if status == "finished" and finished_at is not None:
        return f"Парсер: {moscow_moment(finished_at)}", "ok"
    return "Проверок ещё не было", "idle"


def swapped_order(ids: list[int], first: int, second: int) -> str:
    """Порядок «через запятую» с переставленными соседями — для кнопок «выше/ниже»."""
    order = list(ids)
    order[first], order[second] = order[second], order[first]
    return ",".join(str(value) for value in order)


def count_word(count: int, one: str, few: str, many: str) -> str:
    """«1 товар», «3 товара», «5 товаров»."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} {many}"
    if count % 10 == 1:
        return f"{count} {one}"
    if 2 <= count % 10 <= 4:
        return f"{count} {few}"
    return f"{count} {many}"


def figure(text: str) -> str:
    """Крупная сумма шрифтом Unbounded: между разрядами — обычный неразрывный пробел. Узкий (U+202F, domain.money) в этом
    шрифте со сжатым межбуквенным интервалом почти не виден, и «198 794 ₽» читалось как «198794 ₽» (13.10.2026)."""
    return text.replace("\u202f", "\u00a0")
