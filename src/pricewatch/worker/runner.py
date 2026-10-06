"""Цикл worker: взять задание → выдержать паузу магазина → проверить ссылку → отправить результат.

- Пока идёт проверка, аренда продлевается каждые 30 секунд (аренда — 2 минуты).
- Между запросами к одному магазину — его пауза из задания; у браузерных магазинов (пауза от 5 с)
  к ней добавляются случайные 0–4 с: так ходит человек, а не робот (решение 06.10.2026).
- Ошибка магазина уходит на сервер своим кодом. Неожиданная ошибка парсера записывается в журнал
  с трассировкой и уходит как INTERNAL_ERROR: одна сломанная ссылка не останавливает worker.
- Сервер не принял токен — worker останавливается: без токена работать нельзя.
- Остановка (stop) дожидается конца текущей проверки: результат не теряется.
"""

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.worker.client import LeaseLost, ServerUnavailable, Task
from pricewatch.worker.parsing import Parsed

logger = logging.getLogger(__name__)

HEARTBEAT_EVERY_S = 30.0
BROWSER_DELAY_MS = 5_000
JITTER_MAX_MS = 4_000
SERVER_RETRY_S = 15.0


class Client(Protocol):
    def claim(self) -> Task | None: ...
    def heartbeat(self, task_id: int) -> None: ...
    def result(self, task_id: int, payload: dict[str, Any]) -> None: ...


class Parser(Protocol):
    def parse(self, url: str, region_code: str, parser_key: str, store_host: str, fetch_method: str | None) -> Parsed: ...


class Runner:
    def __init__(
        self,
        client: Client,
        parsers: Parser,
        *,
        poll_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
        heartbeat_every_s: float = HEARTBEAT_EVERY_S,
        jitter: Callable[[int], int] = lambda limit: random.randint(0, limit),
    ) -> None:
        self._client = client
        self._parsers = parsers
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._stopping = threading.Event()
        # Ожидание прерывается остановкой: worker не досиживает паузу, когда его останавливают.
        self._wait = wait or self._stopping.wait
        self._heartbeat_every_s = heartbeat_every_s
        self._jitter = jitter
        self._last_visit: dict[str, float] = {}

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        while not self._stopping.is_set():
            self.step()

    def step(self) -> bool:
        """Одно задание; False — очередь пуста или сервер недоступен (worker подождал)."""
        try:
            task = self._client.claim()
        except ServerUnavailable as error:
            logger.warning("Сервер недоступен, повтор позже", extra={"reason": str(error)})
            self._wait(SERVER_RETRY_S)
            return False
        if task is None:
            self._wait(self._poll_seconds)
            return False
        self._polite_pause(task)
        payload = self._check(task)
        try:
            self._client.result(task.task_id, payload)
        except LeaseLost:
            logger.warning("Аренда задания истекла до отправки результата", extra={"task_id": task.task_id})
        return True

    def _polite_pause(self, task: Task) -> None:
        delay_ms = task.min_delay_ms + (self._jitter(JITTER_MAX_MS) if task.min_delay_ms >= BROWSER_DELAY_MS else 0)
        last = self._last_visit.get(task.store_host)
        if last is not None:
            remaining = delay_ms / 1000 - (self._clock() - last)
            if remaining > 0:
                self._wait(remaining)
        self._last_visit[task.store_host] = self._clock()

    def _check(self, task: Task) -> dict[str, Any]:
        started = self._clock()
        done = threading.Event()
        beat = threading.Thread(target=self._keep_lease, args=(task.task_id, done), daemon=True)
        beat.start()
        try:
            parsed = self._parsers.parse(task.url, task.region_code, task.parser_key, task.store_host, task.fetch_method)
            payload = _success(parsed)
        except PriceWatcherError as error:
            payload = {"status": error.code.value}
        except Exception:
            # Неожиданная ошибка парсера: трассировка — в журнал, на сервер — общий код, worker живёт дальше.
            logger.exception("Неожиданная ошибка проверки ссылки", extra={"task_id": task.task_id, "store": task.store_host})
            payload = {"status": ErrorCode.INTERNAL_ERROR.value}
        finally:
            done.set()
            beat.join()
        payload["duration_ms"] = int((self._clock() - started) * 1000)
        return payload

    def _keep_lease(self, task_id: int, done: threading.Event) -> None:
        while not done.wait(self._heartbeat_every_s):
            try:
                self._client.heartbeat(task_id)
            except (LeaseLost, ServerUnavailable) as error:
                logger.warning("Не удалось продлить аренду", extra={"task_id": task_id, "reason": type(error).__name__})


def _success(parsed: Parsed) -> dict[str, Any]:
    product = parsed.product
    return {
        "status": "ok",
        "price_minor": product.price_minor,
        "card_price_minor": product.card_price_minor,
        "availability": product.availability.value,
        "title": product.title,
        "source": product.source.value,
        "site_name": product.site_name,
        "fetch_method": parsed.fetch_method,
    }
