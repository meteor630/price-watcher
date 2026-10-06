"""Цикл worker с подменёнными клиентом сервера, парсерами и загрузкой фото: результат, ошибки, паузы магазинов, аренда, фото."""

import logging
import threading
from typing import Any

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.contract import ParsedProduct, PriceSource
from pricewatch.media.images import ImageRejected
from pricewatch.worker.client import ImageRefused, LeaseLost, ServerUnavailable, Task, WorkerRejected
from pricewatch.worker.parsing import Parsed
from pricewatch.worker.runner import SERVER_RETRY_S, Runner


def task(task_id: int = 1, host: str = "shop.example", delay_ms: int = 2000, need_image: bool = False) -> Task:
    return Task(task_id, f"https://{host}/p/{task_id}", "spb", host, "universal", None, delay_ms, need_image)


class FakeClient:
    def __init__(self, tasks: list[Task | None | Exception]) -> None:
        self.tasks = list(tasks)
        self.results: list[tuple[int, dict[str, Any]]] = []
        self.heartbeats: list[int] = []
        self.result_error: Exception | None = None
        self.images: list[tuple[int, bytes]] = []
        self.image_error: Exception | None = None
        self.calls: list[str] = []

    def claim(self) -> Task | None:
        item = self.tasks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def heartbeat(self, task_id: int) -> None:
        self.heartbeats.append(task_id)

    def image(self, task_id: int, webp: bytes) -> None:
        self.calls.append("image")
        if self.image_error is not None:
            raise self.image_error
        self.images.append((task_id, webp))

    def result(self, task_id: int, payload: dict[str, Any]) -> None:
        self.calls.append("result")
        if self.result_error is not None:
            raise self.result_error
        self.results.append((task_id, payload))


PRODUCT = ParsedProduct("Лампа", 129_900, 119_900, Availability.IN_STOCK, "https://img.example/1.jpg", "spb", PriceSource.PAGE, "Магазин", ())


class FakeParsers:
    def __init__(self, outcome: object = None, block: threading.Event | None = None) -> None:
        self.outcome = outcome if outcome is not None else Parsed(PRODUCT, "browser")
        self.block = block
        self.calls: list[tuple] = []

    def parse(self, url: str, region_code: str, parser_key: str, store_host: str, fetch_method: str | None) -> Parsed:
        self.calls.append((url, region_code, parser_key, store_host, fetch_method))
        if self.block is not None:
            self.block.wait(5)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


class FakeImages:
    def __init__(self, outcome: bytes | Exception = b"webp") -> None:
        self.outcome = outcome
        self.urls: list[str] = []

    def load(self, image_url: str) -> bytes:
        self.urls.append(image_url)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(round(seconds, 3))
        self.now += seconds
        return False


def runner(client: FakeClient, parsers: FakeParsers, clock: Clock | None = None, images: FakeImages | None = None, **kwargs: Any) -> Runner:
    clock = clock or Clock()
    return Runner(client, parsers, images or FakeImages(), poll_seconds=10, clock=clock, wait=clock.wait, jitter=lambda limit: 1000, **kwargs)


def test_success_result_payload() -> None:
    client = FakeClient([task()])

    assert runner(client, FakeParsers()).step() is True

    (task_id, payload), = client.results
    assert task_id == 1
    assert payload == {
        "status": "ok", "price_minor": 129_900, "card_price_minor": 119_900, "availability": "in_stock", "title": "Лампа",
        "source": "page", "site_name": "Магазин", "fetch_method": "browser", "duration_ms": 0,
    }


def test_store_error_goes_with_its_code() -> None:
    client = FakeClient([task()])

    runner(client, FakeParsers(PriceWatcherError(ErrorCode.CAPTCHA))).step()

    assert client.results[0][1] == {"status": "CAPTCHA", "duration_ms": 0}


def test_unexpected_parser_error_is_logged_and_reported(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient([task(), task(2)])
    parsers = FakeParsers(RuntimeError("сломался разбор"))

    with caplog.at_level(logging.ERROR):
        work = runner(client, parsers)
        work.step()
        work.step()  # worker жив и берёт следующее

    assert [payload["status"] for _, payload in client.results] == ["INTERNAL_ERROR", "INTERNAL_ERROR"]
    record = next(r for r in caplog.records if r.getMessage() == "Неожиданная ошибка проверки ссылки")
    assert record.exc_info is not None


def test_empty_queue_waits_poll_interval() -> None:
    clock = Clock()

    assert runner(FakeClient([None]), FakeParsers(), clock).step() is False
    assert clock.waits == [10]


def test_server_down_waits_and_retries_later() -> None:
    clock = Clock()

    assert runner(FakeClient([ServerUnavailable("down")]), FakeParsers(), clock).step() is False
    assert clock.waits == [SERVER_RETRY_S]


def test_polite_pause_between_requests_to_same_store() -> None:
    clock = Clock()
    client = FakeClient([task(1, "a.example"), task(2, "b.example"), task(3, "a.example")])
    work = runner(client, FakeParsers(), clock)

    work.step()
    work.step()  # другой магазин — без паузы
    work.step()  # снова a.example: 2 с минус прошедшее время (0 по поддельным часам)

    assert clock.waits == [2.0]


def test_browser_stores_get_random_extra_pause() -> None:
    clock = Clock()
    client = FakeClient([task(1, "a.example", 5000), task(2, "a.example", 5000)])
    work = runner(client, FakeParsers(), clock)

    work.step()
    work.step()

    assert clock.waits == [6.0]  # 5 с + 1 с случайной добавки


def test_lease_is_kept_alive_during_long_check() -> None:
    release = threading.Event()
    client = FakeClient([task()])
    parsers = FakeParsers(block=release)
    work = Runner(client, parsers, FakeImages(), poll_seconds=10, heartbeat_every_s=0.01)

    beaten = threading.Event()
    original = client.heartbeat

    def heartbeat(task_id: int) -> None:
        original(task_id)
        beaten.set()

    client.heartbeat = heartbeat  # type: ignore[method-assign]
    thread = threading.Thread(target=work.step)
    thread.start()
    assert beaten.wait(5), "аренда не продлевалась во время долгой проверки"
    release.set()
    thread.join(5)

    assert client.heartbeats and set(client.heartbeats) == {1}
    assert len(client.results) == 1


def test_lost_lease_on_result_does_not_stop_worker(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient([task(), task(2)])
    client.result_error = LeaseLost("/worker/v1/result")
    work = runner(client, FakeParsers())

    with caplog.at_level(logging.WARNING):
        assert work.step() is True
        assert work.step() is True

    assert any(r.getMessage() == "Аренда задания истекла до отправки результата" for r in caplog.records)


def test_rejected_token_stops_worker() -> None:
    work = runner(FakeClient([WorkerRejected("/worker/v1/claim")]), FakeParsers())

    with pytest.raises(WorkerRejected):
        work.run()


def test_stop_ends_loop_after_current_step() -> None:
    client = FakeClient([task(), task(2)])
    work = runner(client, FakeParsers())
    original = client.result

    def result_then_stop(task_id: int, payload: dict[str, Any]) -> None:
        original(task_id, payload)
        work.stop()

    client.result = result_then_stop  # type: ignore[method-assign]
    work.run()

    assert [task_id for task_id, _ in client.results] == [1]


# --- фото ---


def test_image_is_sent_before_result_when_server_needs_it() -> None:
    client, images = FakeClient([task(need_image=True)]), FakeImages(b"RIFF-webp")

    runner(client, FakeParsers(), images=images).step()

    assert images.urls == ["https://img.example/1.jpg"]
    assert client.images == [(1, b"RIFF-webp")]
    assert client.calls == ["image", "result"]  # пока задание в аренде
    assert client.results[0][1]["status"] == "ok"


def test_image_not_loaded_when_link_already_has_one() -> None:
    client, images = FakeClient([task(need_image=False)]), FakeImages()

    runner(client, FakeParsers(), images=images).step()

    assert images.urls == [] and client.calls == ["result"]


def test_failed_check_does_not_load_image() -> None:
    client, images = FakeClient([task(need_image=True)]), FakeImages()

    runner(client, FakeParsers(PriceWatcherError(ErrorCode.CAPTCHA)), images=images).step()

    assert images.urls == []


@pytest.mark.parametrize(
    ("loader", "client_error", "reason"),
    [
        (ImageRejected("unreadable"), None, "unreadable"),
        (PriceWatcherError(ErrorCode.TIMEOUT), None, "TIMEOUT"),
        (b"webp", ImageRefused("/worker/v1/image"), "ImageRefused"),
        (b"webp", ServerUnavailable("down"), "ServerUnavailable"),
    ],
)
def test_image_failure_is_logged_and_price_still_sent(
    caplog: pytest.LogCaptureFixture, loader: bytes | Exception, client_error: Exception | None, reason: str
) -> None:
    client = FakeClient([task(need_image=True)])
    client.image_error = client_error

    with caplog.at_level(logging.WARNING):
        runner(client, FakeParsers(), images=FakeImages(loader)).step()

    record = next(r for r in caplog.records if r.getMessage() == "Фото не загружено")
    assert record.reason == reason  # type: ignore[attr-defined]
    assert client.results[0][1]["status"] == "ok"


def test_unexpected_image_error_is_logged_with_trace(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient([task(need_image=True)])

    with caplog.at_level(logging.ERROR):
        runner(client, FakeParsers(), images=FakeImages(MemoryError())).step()

    record = next(r for r in caplog.records if r.getMessage() == "Неожиданная ошибка загрузки фото")
    assert record.exc_info is not None
    assert client.results[0][1]["status"] == "ok"
