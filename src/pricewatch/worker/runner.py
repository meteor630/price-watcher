"""Обработка одного взятого задания worker: выдержать паузу магазина → проверить ссылку → (фото) → отправить результат.

Задания берёт диспетчер (lanes.py): у каждого магазина своя полоса со своим Runner.
- Пока идёт проверка, аренда продлевается каждые 30 секунд (аренда — 2 минуты).
- Между запросами к одному магазину — его пауза из задания; у браузерных магазинов (пауза от 5 с)
  к ней добавляются случайные 0–4 с: так ходит человек, а не робот (решение 06.10.2026).
- Магазин отказал (BLOCKED, CAPTCHA) — следующая пауза дольше: пауза × 2^(отказов подряд), не больше 10 минут;
  удачная проверка сбрасывает счётчик. Остановку магазина на прогон после серии отказов делает сервер (пауза магазина).
- Ошибка магазина уходит на сервер своим кодом. Неожиданная ошибка парсера записывается в журнал
  с трассировкой и уходит как INTERNAL_ERROR: одна сломанная ссылка не останавливает worker.
- Фото скачивается, только если сервер отметил в задании, что его у ссылки нет, и проверка удалась.
  Сбой фото пишется в журнал и не мешает цене: результат уходит как обычно.
- Сервер может быть захвачен: перед проверкой адрес задания проходит локальный пропуск (allowlist.EgressGate —
  домен из списка домашнего узла, публичный адрес); отказ уходит на сервер кодом DOMAIN_NOT_ALLOWED или INVALID_URL.
  Список доменов не читается — AllowlistUnusable поднимается дальше, и worker останавливается.
"""

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.media.images import ImageRejected
from pricewatch.worker.allowlist import AllowlistUnusable
from pricewatch.worker.client import ImageRefused, LeaseLost, ServerUnavailable, Task
from pricewatch.worker.parsing import Parsed

logger = logging.getLogger(__name__)

HEARTBEAT_EVERY_S = 30.0
BROWSER_DELAY_MS = 5_000
JITTER_MAX_MS = 4_000
SERVER_RETRY_S = 15.0
BACKOFF_MAX_MS = 600_000
REFUSALS = frozenset({ErrorCode.BLOCKED.value, ErrorCode.CAPTCHA.value})


class Client(Protocol):
    def claim(self) -> Task | None: ...
    def heartbeat(self, task_id: int) -> None: ...
    def image(self, task_id: int, webp: bytes) -> None: ...
    def result(self, task_id: int, payload: dict[str, Any]) -> None: ...


class Parser(Protocol):
    def parse(self, url: str, region_code: str, parser_key: str, store_host: str, fetch_method: str | None) -> Parsed: ...


class ImageSource(Protocol):
    def load(self, image_url: str) -> bytes: ...


class Gate(Protocol):
    def check(self, url: str) -> None:
        """Адрес можно открывать; иначе PriceWatcherError с кодом для сервера."""
        ...


class Limit(Protocol):
    """Суточный лимит заданий: проверить перед вопросом серверу, отметить взятое (limits.DailyTaskLimit)."""

    def ensure_room(self) -> None: ...
    def taken(self) -> None: ...


class Runner:
    def __init__(
        self,
        client: Client,
        parsers: Parser,
        images: ImageSource,
        *,
        gate: Gate,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
        heartbeat_every_s: float = HEARTBEAT_EVERY_S,
        jitter: Callable[[int], int] = lambda limit: random.randint(0, limit),
    ) -> None:
        self._client = client
        self._parsers = parsers
        self._images = images
        self._gate = gate
        self._clock = clock
        # Ожидание паузы; диспетчер передаёт своё — оно прерывается остановкой worker.
        self._wait = wait or threading.Event().wait
        self._heartbeat_every_s = heartbeat_every_s
        self._jitter = jitter
        self._last_visit: dict[str, float] = {}
        self._refusals: dict[str, int] = {}

    def process(self, task: Task) -> None:
        """Взятое задание: пауза магазина → проверка → (фото) → результат."""
        self._polite_pause(task)
        payload = self._check(task)
        try:
            self._client.result(task.task_id, payload)
        except LeaseLost:
            logger.warning("Аренда задания истекла до отправки результата", extra={"task_id": task.task_id})
        if payload["status"] in REFUSALS:
            self._refusals[task.store_host] = self._refusals.get(task.store_host, 0) + 1
        else:
            self._refusals.pop(task.store_host, None)
        logger.info(
            "Проверка ссылки завершена",
            extra={"task_id": task.task_id, "store": task.store_host,
                   "duration_ms": payload["duration_ms"], "result": payload["status"]},
        )

    def _polite_pause(self, task: Task) -> None:
        delay_ms = task.min_delay_ms + (self._jitter(JITTER_MAX_MS) if task.min_delay_ms >= BROWSER_DELAY_MS else 0)
        refusals = self._refusals.get(task.store_host, 0)
        if refusals:
            delay_ms = min(BACKOFF_MAX_MS, delay_ms * 2**refusals)
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
            payload, image_url = self._parse(task)
            if task.need_image and image_url is not None:
                self._send_image(task, image_url)
        finally:
            done.set()
            beat.join()
        payload["duration_ms"] = int((self._clock() - started) * 1000)
        return payload

    def _parse(self, task: Task) -> tuple[dict[str, Any], str | None]:
        try:
            self._gate.check(task.url)
            parsed = self._parsers.parse(task.url, task.region_code, task.parser_key, task.store_host, task.fetch_method)
        except PriceWatcherError as error:
            return {"status": error.code.value}, None
        except AllowlistUnusable:
            raise  # без списка доменов работать нельзя: worker останавливается
        except Exception:
            # Неожиданная ошибка парсера: трассировка — в журнал, на сервер — общий код, worker живёт дальше.
            logger.exception("Неожиданная ошибка проверки ссылки", extra={"task_id": task.task_id, "store": task.store_host})
            return {"status": ErrorCode.INTERNAL_ERROR.value}, None
        return _success(parsed), parsed.product.image_url

    def _send_image(self, task: Task, image_url: str) -> None:
        extra: dict[str, Any] = {"task_id": task.task_id, "store": task.store_host}
        try:
            self._client.image(task.task_id, self._images.load(image_url))
        except ImageRejected as error:
            logger.warning("Фото не загружено", extra=extra | {"reason": error.reason})
        except PriceWatcherError as error:
            logger.warning("Фото не загружено", extra=extra | {"reason": error.code.value})
        except (ImageRefused, LeaseLost, ServerUnavailable) as error:
            logger.warning("Фото не загружено", extra=extra | {"reason": type(error).__name__})
        except Exception:
            # Сбой фото не должен отнимать у ссылки цену: трассировка — в журнал, результат уходит дальше.
            logger.exception("Неожиданная ошибка загрузки фото", extra=extra)

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
