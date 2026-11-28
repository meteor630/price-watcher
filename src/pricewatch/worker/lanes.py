"""Параллельная проверка по магазинам (часть А, срезы А1–А2): у каждого магазина своя «полоса».

- Полоса — поток со своим Runner, своими парсерами и своим профилем Chrome. Синхронный Playwright привязан
  к потоку, поэтому браузер магазина живёт в его полосе и не перезапускается при смене магазина: между заданиями
  разных магазинов ничего не закрывается. Полоса без заданий дольше IDLE_S закрывает свой браузер и завершается.
- Диспетчер берёт задания у сервера, пока заняты не все полосы (lanes). Сервер не выдаёт задание магазина, у которого
  уже есть действующая аренда, — поэтому внутри магазина проверки идут строго по очереди со своей паузой
  (Runner._polite_pause), а разные магазины — одновременно. Ручная проверка с повышенным приоритетом выдаётся
  первой при ближайшем свободном месте.
- Общий лимит вкладок браузера держит семафор, общий для всех полос (BrowserFetcher, настройка worker).
- Суточный лимит и вопрос серверу — только в диспетчере (один поток). Ошибка, после которой работать нельзя
  (токен не принят, ответ сервера не по схеме, список доменов не читается), останавливает всё: текущие проверки
  дорабатывают, новые не берутся, ошибка поднимается из run(). stop() — то же без ошибки.
"""

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from pricewatch.worker.client import ServerUnavailable, Task
from pricewatch.worker.runner import SERVER_RETRY_S, Client, Limit

logger = logging.getLogger(__name__)

IDLE_S = 120.0


class Processor(Protocol):
    """То, что полосе нужно от Runner: обработать взятое задание и закрыть свои ресурсы (браузер, клиенты)."""

    def process(self, task: Task) -> None: ...
    def close(self) -> None: ...


@dataclass
class _Lane:
    host: str
    tasks: "queue.Queue[Task | None]" = field(default_factory=queue.Queue)
    busy: bool = True
    thread: threading.Thread | None = None


class Dispatcher:
    def __init__(
        self, client: Client, make_processor: Callable[[Callable[[float], bool]], Processor], limit: Limit, *, lanes: int, poll_seconds: float,
        idle_s: float = IDLE_S,
    ) -> None:
        self._client = client
        self._make_processor = make_processor
        self._limit = limit
        self._max_lanes = lanes
        self._poll_seconds = poll_seconds
        self._idle_s = idle_s
        self._lanes: dict[str, _Lane] = {}
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._stopping = threading.Event()
        self._fatal: BaseException | None = None

    def stop(self) -> None:
        self._stopping.set()
        self._changed.set()

    def run(self) -> None:
        try:
            while not self._stopping.is_set():
                if self._busy() >= self._max_lanes:
                    self._wait(self._poll_seconds)
                    continue
                self._limit.ensure_room()
                try:
                    task = self._client.claim()
                except ServerUnavailable as error:
                    logger.warning("Сервер недоступен, повтор позже", extra={"reason": str(error)})
                    self._wait(SERVER_RETRY_S)
                    continue
                if task is None:
                    self._wait(self._poll_seconds)
                    continue
                self._limit.taken()
                self._route(task)
        finally:
            self._stopping.set()
            self._finish_lanes()
        if self._fatal is not None:
            raise self._fatal

    def _busy(self) -> int:
        with self._lock:
            return sum(1 for lane in self._lanes.values() if lane.busy)

    def _wait(self, seconds: float) -> None:
        self._changed.wait(seconds)
        self._changed.clear()

    def _route(self, task: Task) -> None:
        with self._lock:
            lane = self._lanes.get(task.store_host)
            lane_is_new = lane is None
            if lane is None:
                lane = _Lane(task.store_host)
                lane.thread = threading.Thread(name=f"lane-{task.store_host}", daemon=True, target=self._lane_loop, args=(lane,))
                self._lanes[task.store_host] = lane
            lane.busy = True
            lane.tasks.put(task)
        if lane_is_new and lane.thread is not None:
            lane.thread.start()

    def _lane_loop(self, lane: _Lane) -> None:
        # Паузы магазина в полосе прерываются остановкой worker.
        processor = self._make_processor(self._stopping.wait)
        try:
            while True:
                try:
                    task = lane.tasks.get(timeout=self._idle_s)
                except queue.Empty:
                    with self._lock:
                        # Задание могли положить, пока полоса решала завершиться: тогда она работает дальше.
                        if lane.tasks.empty():
                            del self._lanes[lane.host]
                            return
                    continue
                if task is None:
                    return
                try:
                    processor.process(task)
                except BaseException as error:
                    logger.error("Полоса магазина остановлена ошибкой", extra={"store": lane.host, "reason": type(error).__name__})
                    with self._lock:
                        self._fatal = self._fatal or error
                    self.stop()
                    return
                finally:
                    with self._lock:
                        lane.busy = not lane.tasks.empty()
                    self._changed.set()
        finally:
            processor.close()

    def _finish_lanes(self) -> None:
        """Остановка: текущие проверки дорабатывают, полосы закрывают браузеры."""
        with self._lock:
            lanes = list(self._lanes.values())
            for lane in lanes:
                lane.tasks.put(None)
        for lane in lanes:
            if lane.thread is not None:
                lane.thread.join()
