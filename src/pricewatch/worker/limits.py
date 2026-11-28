"""Суточный лимит заданий домашнего worker (слой 1 изоляции): сколько заданий в сутки worker вообще возьмёт.

Число задаёт владелец локально (PRICEWATCH_WORKER_DAILY_TASK_LIMIT, ≈ 3 × число активных ссылок), а не сервер:
захваченный сервер не превратит домашний IP в чужого бесконечного сборщика. Сутки — по Москве. Счётчик лежит в файле
рядом с профилями браузера и переживает перезапуск worker. Лимит исчерпан — worker останавливается (код 5, автозапуск
его не перезапускает); сторож сервера через 5 минут молчания сообщает администраторам, что worker не на связи.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pricewatch.domain.days import MOSCOW

logger = logging.getLogger(__name__)


class DailyLimitReached(Exception):
    """Worker взял столько заданий за сутки, сколько разрешил владелец."""


class DailyTaskLimit:
    def __init__(self, limit: int, path: Path, clock: Callable[[], datetime]) -> None:
        self._limit = limit
        self._path = path
        self._clock = clock

    def ensure_room(self) -> None:
        """Перед вопросом серверу: лимит не исчерпан, иначе DailyLimitReached."""
        if self._count() >= self._limit:
            logger.error("Суточный лимит заданий исчерпан — worker останавливается", extra={"limit": self._limit})
            raise DailyLimitReached(str(self._limit))

    def taken(self) -> None:
        """Задание получено: счётчик суток +1."""
        today = self._today()
        count = self._count() + 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"day": today, "count": count}), encoding="utf-8")

    def _today(self) -> str:
        return self._clock().astimezone(MOSCOW).date().isoformat()

    def _count(self) -> int:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError):
            # Испорченный счётчик не обнуляет лимит: считаем, что сутки уже исчерпаны, пока владелец не разберётся.
            logger.error("Счётчик заданий не читается", extra={"path": str(self._path)}, exc_info=True)
            return self._limit
        if not isinstance(data, dict) or data.get("day") != self._today():
            return 0
        count = data.get("count")
        return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else self._limit
