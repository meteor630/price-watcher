"""Диспетчер worker: разные магазины проверяются одновременно, задания одного магазина — по очереди в его полосе;
полоса (и её браузер) не пересоздаётся при смене магазина; не больше lanes полос сразу; простаивающая полоса закрывается;
суточный лимит — до вопроса серверу; ошибка, после которой работать нельзя, останавливает всё."""

import threading
import time
from collections.abc import Callable

import pytest

from pricewatch.worker.client import ServerUnavailable, Task, WorkerRejected
from pricewatch.worker.lanes import Dispatcher
from pricewatch.worker.limits import DailyLimitReached

WAIT_S = 5.0


def task(task_id: int, host: str) -> Task:
    return Task(task_id, f"https://{host}/p/{task_id}", "spb", host, "universal", None, 0, False)


class Server:
    """Выдаёт задания по списку; кончились — останавливает диспетчер (как пустая очередь в конце прогона)."""

    def __init__(self, items: list[Task | Exception]) -> None:
        self.items = list(items)
        self.claims = 0
        self.dispatcher: Dispatcher | None = None

    def claim(self) -> Task | None:
        self.claims += 1
        if not self.items:
            assert self.dispatcher is not None
            self.dispatcher.stop()
            return None
        item = self.items.pop(0)
        if isinstance(item, ServerUnavailable) and self.dispatcher is not None:
            self.dispatcher.stop()
        if isinstance(item, Exception):
            raise item
        return item

    def heartbeat(self, task_id: int) -> None: ...
    def image(self, task_id: int, webp: bytes) -> None: ...
    def result(self, task_id: int, payload: dict) -> None: ...


class Recorder:
    """Полосы: какие задания в каком потоке, сколько обрабатываются одновременно, сколько полос создано и закрыто."""

    def __init__(self, hold: Callable[[Task], None] = lambda task: None) -> None:
        self.hold = hold
        self.lock = threading.Lock()
        self.done: list[tuple[int, str, int]] = []
        self.created: list[int] = []
        self.closed: list[int] = []
        self.running = 0
        self.peak = 0

    def make(self, wait: Callable[[float], bool]) -> "RecorderLane":
        with self.lock:
            self.created.append(threading.get_ident())
        return RecorderLane(self)


class RecorderLane:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def process(self, task: Task) -> None:
        with self.recorder.lock:
            self.recorder.running += 1
            self.recorder.peak = max(self.recorder.peak, self.recorder.running)
        try:
            self.recorder.hold(task)
        finally:
            with self.recorder.lock:
                self.recorder.running -= 1
                self.recorder.done.append((task.task_id, task.store_host, threading.get_ident()))

    def close(self) -> None:
        with self.recorder.lock:
            self.recorder.closed.append(threading.get_ident())


class Limit:
    def __init__(self, limit: int = 1000) -> None:
        self.limit = limit
        self.count = 0

    def ensure_room(self) -> None:
        if self.count >= self.limit:
            raise DailyLimitReached(str(self.limit))

    def taken(self) -> None:
        self.count += 1


def run(server: Server, recorder: Recorder, *, lanes: int = 4, limit: Limit | None = None, idle_s: float = 60.0) -> Dispatcher:
    dispatcher = Dispatcher(server, recorder.make, limit or Limit(), lanes=lanes, poll_seconds=0.01, idle_s=idle_s)
    server.dispatcher = dispatcher
    worker = threading.Thread(target=dispatcher.run, daemon=True)
    worker.start()
    worker.join(WAIT_S)
    assert not worker.is_alive(), "диспетчер не остановился"
    return dispatcher


# --- обычный путь ---


def test_different_stores_run_at_the_same_time() -> None:
    both_started = threading.Barrier(2, timeout=WAIT_S)
    recorder = Recorder(hold=lambda task: both_started.wait())

    run(Server([task(1, "dns-shop.ru"), task(2, "ozon.ru")]), recorder)

    assert recorder.peak == 2
    assert sorted(task_id for task_id, _, _ in recorder.done) == [1, 2]


def test_one_lane_per_store_survives_store_switches() -> None:
    # Магазины вперемешку: браузер магазина не пересоздаётся — полос (и профилей Chrome) ровно две.
    recorder = Recorder()
    items = [task(1, "a.ru"), task(2, "b.ru"), task(3, "a.ru"), task(4, "b.ru"), task(5, "a.ru")]

    run(Server(items), recorder)

    assert len(recorder.created) == 2
    threads = {host: {thread for _, h, thread in recorder.done if h == host} for host in ("a.ru", "b.ru")}
    assert all(len(found) == 1 for found in threads.values())
    order_a = [task_id for task_id, host, _ in recorder.done if host == "a.ru"]
    assert order_a == [1, 3, 5]  # внутри магазина — строго по очереди


# --- граница: число полос, простой ---


def test_no_more_lanes_than_allowed_at_once() -> None:
    recorder = Recorder(hold=lambda task: time.sleep(0.02))

    run(Server([task(i, f"shop{i}.ru") for i in range(1, 7)]), recorder, lanes=2)

    assert recorder.peak <= 2
    assert len(recorder.done) == 6


def test_idle_lane_closes_its_browser() -> None:
    recorder = Recorder()
    items = [task(1, "a.ru"), task(2, "b.ru")]
    server = Server([])
    server.claim = lambda: items.pop(0) if items else None  # type: ignore[method-assign]
    dispatcher = Dispatcher(server, recorder.make, Limit(), lanes=2, poll_seconds=0.05, idle_s=0.05)
    stopper = threading.Timer(0.5, dispatcher.stop)
    stopper.start()

    dispatcher.run()

    # Обе полосы простаивали дольше idle_s и закрыли свои браузеры ещё до остановки worker.
    assert len(recorder.closed) == 2 and len(recorder.done) == 2


# --- отказ: лимит, сервер, ошибки, после которых работать нельзя ---


def test_daily_limit_stops_before_asking_server() -> None:
    server = Server([task(1, "a.ru"), task(2, "b.ru"), task(3, "c.ru")])
    limit = Limit(limit=2)
    dispatcher = Dispatcher(server, Recorder().make, limit, lanes=4, poll_seconds=0.01)
    server.dispatcher = dispatcher

    with pytest.raises(DailyLimitReached):
        dispatcher.run()

    assert server.claims == 2 and limit.count == 2


def test_empty_queue_does_not_spend_daily_limit() -> None:
    limit = Limit()
    run(Server([]), Recorder(), limit=limit)
    assert limit.count == 0


def test_server_down_is_not_fatal() -> None:
    recorder = Recorder()
    run(Server([ServerUnavailable("down")]), recorder)
    assert recorder.done == []


def test_rejected_token_on_claim_stops_worker() -> None:
    server = Server([WorkerRejected("/worker/v1/claim")])
    dispatcher = Dispatcher(server, Recorder().make, Limit(), lanes=2, poll_seconds=0.01)
    server.dispatcher = dispatcher

    with pytest.raises(WorkerRejected):
        dispatcher.run()


def test_fatal_error_in_lane_stops_everything_after_current_checks() -> None:
    def hold(task: Task) -> None:
        if task.store_host == "bad.ru":
            raise WorkerRejected("/worker/v1/result")

    recorder = Recorder(hold=hold)
    server = Server([task(1, "bad.ru"), task(2, "ok.ru"), task(3, "ok.ru")])
    dispatcher = Dispatcher(server, recorder.make, Limit(), lanes=1, poll_seconds=0.01)
    server.dispatcher = dispatcher

    with pytest.raises(WorkerRejected):
        dispatcher.run()

    assert [task_id for task_id, _, _ in recorder.done] == [1]  # новых заданий после ошибки не брали
    assert len(recorder.closed) == len(recorder.created)  # браузеры закрыты
