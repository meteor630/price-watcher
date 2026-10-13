"""Резервные копии (DEVELOPMENT_ARCHITECTURE.md, раздел 11): имя файла копии и срок хранения.

- Имя — момент создания по UTC с секундами: pricewatch-2026-10-14T013000Z.tar.age. По нему считается возраст копии:
  время изменения файла на диске Windows, подключённом в контейнер, ненадёжно.
- Копия хранится 30 суток; старше — удаляется. Файлы с другими именами в папке копий не трогаются.
- Удачной копии нет больше 36 часов — предупреждение администраторам. Отсчёт — от последней удачной копии, а если
  удачных не было — от первой попытки; попыток нет вовсе — копии на узле не настроены, предупреждать не о чем.
"""

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

KEEP = timedelta(days=30)
STALE_AFTER = timedelta(hours=36)
NAME = re.compile(r"^pricewatch-(\d{4}-\d{2}-\d{2}T\d{6})Z\.tar\.age$")
NAME_TIME = "%Y-%m-%dT%H%M%S"


def backup_name(created_at: datetime) -> str:
    if created_at.tzinfo is None:
        raise ValueError("время копии без часового пояса")
    return f"pricewatch-{created_at.astimezone(UTC).strftime(NAME_TIME)}Z.tar.age"


def created_at(name: str) -> datetime | None:
    """Момент создания по имени копии; None — файл не копия Price Watcher."""
    match = NAME.match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), NAME_TIME).replace(tzinfo=UTC)
    except ValueError:
        return None


def expired(names: Iterable[str], now: datetime, keep: timedelta = KEEP) -> list[str]:
    """Копии старше срока хранения, по возрастанию времени создания."""
    dated = [(moment, name) for name in names if (moment := created_at(name)) is not None]
    return [name for moment, name in sorted(dated) if moment < now - keep]


def stale_since(last_ok_at: datetime | None, first_started_at: datetime | None, now: datetime) -> datetime | None:
    """С какого момента нет удачной копии, если дольше 36 часов; иначе None. Ровно 36 часов — ещё не повод."""
    reference = last_ok_at or first_started_at
    if reference is None or now - reference <= STALE_AFTER:
        return None
    return reference
