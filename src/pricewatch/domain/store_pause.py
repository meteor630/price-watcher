"""Пауза магазина после блокировки или капчи (DEVELOPMENT_ARCHITECTURE.md, разделы 7 и 8).

- 403 или 429 (BLOCKED): отдых 15 минут, затем 1 час, затем до следующего планового запуска.
- CAPTCHA: паузы короче — 15 секунд, затем 60 секунд, затем до следующего планового запуска.
- Ступень растёт, пока магазин отвечает той же причиной; другая причина или ступени, уже
  исчерпанные в прошлый раз, начинают лестницу сначала. Любой другой ответ магазина снимает паузу.
- Пока ступени не исчерпаны, задание ждёт в очереди и ошибкой не считается.
"""

from dataclasses import dataclass
from datetime import timedelta

from pricewatch.domain.errors import ErrorCode

STEPS = {
    ErrorCode.BLOCKED: (timedelta(minutes=15), timedelta(hours=1)),
    ErrorCode.CAPTCHA: (timedelta(seconds=15), timedelta(seconds=60)),
}
PAUSE_REASONS = frozenset(STEPS)


@dataclass(frozen=True, slots=True)
class PauseStep:
    level: int
    # None — ступени исчерпаны: магазин отдыхает до следующего планового запуска.
    delay: timedelta | None

    @property
    def exhausted(self) -> bool:
        return self.delay is None


def next_step(reason: ErrorCode, previous_reason: str | None, previous_level: int) -> PauseStep:
    steps = STEPS[reason]
    if previous_reason != reason.value or previous_level > len(steps):
        level = 1
    else:
        level = previous_level + 1
    return PauseStep(level, steps[level - 1] if level <= len(steps) else None)
