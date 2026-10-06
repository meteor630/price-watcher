"""Ступени паузы магазина: 403/429 — 15 мин, 1 ч, до запуска; капча — 15 с, 60 с, до запуска."""

from datetime import timedelta

import pytest

from pricewatch.domain.errors import ErrorCode
from pricewatch.domain.store_pause import PauseStep, next_step


def ladder(reason: ErrorCode, times: int) -> list[PauseStep]:
    steps, previous_reason, level = [], None, 0
    for _ in range(times):
        step = next_step(reason, previous_reason, level)
        steps.append(step)
        previous_reason, level = reason.value, step.level
    return steps


def test_blocked_ladder() -> None:
    assert [step.delay for step in ladder(ErrorCode.BLOCKED, 3)] == [timedelta(minutes=15), timedelta(hours=1), None]


def test_captcha_ladder_is_shorter() -> None:
    assert [step.delay for step in ladder(ErrorCode.CAPTCHA, 3)] == [timedelta(seconds=15), timedelta(seconds=60), None]


def test_exhausted_only_on_third() -> None:
    assert [step.exhausted for step in ladder(ErrorCode.CAPTCHA, 3)] == [False, False, True]


def test_after_exhausted_rest_ladder_starts_again() -> None:
    assert ladder(ErrorCode.BLOCKED, 4)[3] == PauseStep(1, timedelta(minutes=15))


@pytest.mark.parametrize(("previous", "level"), [("CAPTCHA", 1), ("CAPTCHA", 2), (None, 0)])
def test_other_reason_starts_from_first_step(previous: str | None, level: int) -> None:
    assert next_step(ErrorCode.BLOCKED, previous, level) == PauseStep(1, timedelta(minutes=15))


def test_only_blocked_and_captcha_pause_a_store() -> None:
    with pytest.raises(KeyError):
        next_step(ErrorCode.TIMEOUT, None, 0)
