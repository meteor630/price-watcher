"""Расписание плановых проверок (DEVELOPMENT_ARCHITECTURE.md, раздел 8).

- По умолчанию одна проверка в 09:00 по Москве; администратор задаёт до трёх времён.
- Время хранится строкой «ЧЧ:ММ»; повтор и неверная запись — ошибка, а не молчаливая замена.
- Слот — московская дата и время проверки, ключ вида «2026-10-05T09:00»: по нему база не даёт
  создать плановый запуск дважды, даже если планировщиков два.
- Наступивший слот — последний из промежутка (после, сейчас]. Планировщик после старта берёт
  промежуток от начала текущих суток: так выполняется один пропущенный запуск за сутки.
- Следующий запуск — ближайший слот позже текущего момента: до него отдыхает магазин,
  исчерпавший паузы.
"""

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from pricewatch.domain.days import MOSCOW, moscow_date

DEFAULT_TIMES = (time(9, 0),)
TIMES_MAX = 3
TIME_FORMAT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_times(raw: object) -> tuple[time, ...]:
    """Времена проверок из настройки: список строк «ЧЧ:ММ», от одного до трёх, без повторов; по возрастанию."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= TIMES_MAX:
        raise ValueError(f"нужен список от 1 до {TIMES_MAX} времён")
    times = []
    for value in raw:
        match = TIME_FORMAT.match(value) if isinstance(value, str) else None
        if match is None:
            raise ValueError("время проверки — строка ЧЧ:ММ")
        times.append(time(int(match.group(1)), int(match.group(2))))
    if len(set(times)) != len(times):
        raise ValueError("время проверки повторяется")
    return tuple(sorted(times))


def slot_key(day: date, at: time) -> str:
    return f"{day.isoformat()}T{at.strftime('%H:%M')}"


def due_slot(times: Sequence[time], after: datetime, now: datetime) -> str | None:
    """Ключ последнего слота в промежутке (after, now]; None — ни один не наступил."""
    day = moscow_date(after)
    due: tuple[datetime, str] | None = None
    while day <= moscow_date(now):
        for at in times:
            moment = datetime.combine(day, at, MOSCOW)
            if after < moment <= now and (due is None or moment > due[0]):
                due = (moment, slot_key(day, at))
        day += timedelta(days=1)
    return None if due is None else due[1]


def next_slot_at(times: Sequence[time], now: datetime) -> datetime:
    """Момент ближайшего слота позже `now` (сегодня или завтра по Москве), в UTC."""
    today = moscow_date(now)
    moments = [datetime.combine(today + timedelta(days=shift), at, MOSCOW) for shift in (0, 1) for at in times]
    return min(moment for moment in moments if moment > now).astimezone(UTC)
