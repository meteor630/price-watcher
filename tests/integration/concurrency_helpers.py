"""Одновременные транзакции с явным пересечением — общее для интеграционных тестов.

overlapped(engine, first, second): первая транзакция выполняет first и держит транзакцию,
вторая в другом потоке выполняет second и упирается в блокировку (это проверяется
по pg_stat_activity), и только потом первая фиксируется.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, text

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

WAIT_S = 10


@dataclass
class Outcome:
    value: object = None
    error: ErrorCode | None = None


def committed(engine: Engine, action: Callable[[SqlUnitOfWork], object]) -> object:
    with SqlUnitOfWork(engine) as work:
        result = action(work)
        work.commit()
        return result


def attempt(engine: Engine, action: Callable[[SqlUnitOfWork], object]) -> Outcome:
    try:
        return Outcome(value=committed(engine, action))
    except PriceWatcherError as error:
        return Outcome(error=error.code)


def lock_waiters(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        ).scalar_one()


def wait_for(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"Не дождались: {what}")
        time.sleep(0.02)


class HeldWork:
    """Уже открытая транзакция помощника overlapped: сценарий не открывает и не фиксирует её сам."""

    def __init__(self, work: SqlUnitOfWork) -> None:
        self._work = work

    def __getattr__(self, name: str) -> object:
        return getattr(self._work, name)

    def __enter__(self) -> "HeldWork":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def commit(self) -> None:
        return None


def overlapped(
    engine: Engine, first: Callable[[SqlUnitOfWork], object], second: Callable[[SqlUnitOfWork], object]
) -> tuple[Outcome, Outcome]:
    """first выполняется и держит транзакцию; second упирается в блокировку; затем first фиксируется."""
    holding, release = threading.Event(), threading.Event()
    first_outcome = Outcome()

    def run_first() -> None:
        nonlocal first_outcome
        with SqlUnitOfWork(engine) as work:
            first_outcome = Outcome(value=first(work))
            holding.set()
            release.wait(WAIT_S)
            work.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(run_first)
        assert holding.wait(WAIT_S), "первая транзакция не дошла до удержания"
        waiters_before = lock_waiters(engine)
        second_future = pool.submit(attempt, engine, second)
        wait_for(lambda: lock_waiters(engine) > waiters_before, "вторая транзакция ждёт блокировку")
        release.set()
        first_future.result(WAIT_S)
        return first_outcome, second_future.result(WAIT_S)
