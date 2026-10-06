"""Настоящий Chrome через patchright ходит в сеть только через проверяющий прокси.

На странице — запросы во все стороны к «ловушке»: локальному серверу, который считает соединения.
Скрипты, стили, фреймы, fetch, WebSocket, всплывающее окно, http и https, по IP и по имени —
ни одно соединение не должно дойти до ловушки. Интернет не нужен. Нужен установленный Google Chrome
и экран (на Linux в CI — Xvfb).
"""

import ipaddress
import logging
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.browser import BrowserFetcher, BrowserPage, looks_blocked
from pricewatch.parsers.urls import normalize_url


class Trap(socketserver.BaseRequestHandler):
    hits = 0

    def handle(self) -> None:
        Trap.hits += 1


@pytest.fixture
def trap() -> Iterator[int]:
    Trap.hits = 0
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Trap)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield int(server.server_address[1])
    server.shutdown()
    server.server_close()


@pytest.fixture
def fetcher(tmp_path: Path) -> Iterator[BrowserFetcher]:
    table = {"trap.example": [ipaddress.ip_address("127.0.0.1")], "internal.example": [ipaddress.ip_address("10.0.0.1")]}
    browser = BrowserFetcher(tmp_path / "profiles", resolver=lambda host: table.get(host, []))
    yield browser
    browser.close()


def test_every_browser_request_goes_through_proxy(fetcher: BrowserFetcher, trap: int, caplog: pytest.LogCaptureFixture) -> None:
    import socket

    with socket.create_connection(("127.0.0.1", trap)):
        pass  # контроль: ловушка доступна напрямую
    targets = [f"http://127.0.0.1:{trap}", f"https://127.0.0.1:{trap}", f"http://localhost:{trap}", f"http://trap.example:{trap}"]
    html = "".join(
        f'<script src="{t}/s.js"></script><link rel="stylesheet" href="{t}/s.css"><iframe src="{t}/frame"></iframe>'
        for t in targets
    ) + (
        "<script>"
        + "".join(f"fetch('{t}/api').catch(() => 0);" for t in targets)
        + f"try {{ new WebSocket('ws://127.0.0.1:{trap}/ws') }} catch (e) {{}}"
        + f"window.open('http://127.0.0.1:{trap}/popup');"
        + "</script>"
    )

    with caplog.at_level(logging.INFO, logger="pricewatch.parsers.egress"):
        tab = fetcher.context("trap-test").new_page()
        tab.set_content(html)
        tab.wait_for_timeout(3000)

    assert Trap.hits == 1  # только контрольное соединение из теста
    assert any(record.getMessage() == "Браузер: запрос отклонён" for record in caplog.records)  # запросы дошли до прокси


def test_page_on_internal_address_is_refused_before_opening(fetcher: BrowserFetcher) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        fetcher.fetch(normalize_url("https://internal.example/p"), "test", lambda page: True)

    assert caught.value.code is ErrorCode.INVALID_URL


# --- логика повтора без браузера ---


def page(status: int = 200, html: str = "<html>товар</html>") -> BrowserPage:
    return BrowserPage(normalize_url("https://shop.example/p"), "https://shop.example/p", status, html)


@pytest.mark.parametrize(
    ("status", "html", "blocked"),
    [
        (498, "", True),
        (403, "<html></html>", True),
        (429, "", True),
        (200, "<html><title>Проверка браузера</title></html>", True),
        (200, "<script src='/qrator/qauth.js'></script>", True),
        (200, "<html><h1>Лампа</h1></html>", False),
        (404, "<html>нет такого</html>", False),
    ],
)
def test_looks_blocked(status: int, html: str, blocked: bool) -> None:
    assert looks_blocked(page(status, html)) is blocked


def run_with(fetcher: BrowserFetcher, monkeypatch: pytest.MonkeyPatch, pages: list[BrowserPage]) -> tuple[BrowserPage, list[str]]:
    events: list[str] = []
    queue = iter(pages)

    def fake_open(url, store_key, ready, timeout_s):  # type: ignore[no-untyped-def]
        events.append("open")
        return next(queue)

    monkeypatch.setattr(fetcher, "_open", fake_open)
    monkeypatch.setattr(fetcher, "_reset_profile", lambda: events.append("reset"))
    monkeypatch.setattr("pricewatch.parsers.browser.public_address", lambda host, resolver: object())
    result = fetcher.fetch(normalize_url("https://shop.example/p"), "shop", lambda p: "товар" in p.html)
    return result, events


def test_ready_page_is_returned_at_once(fetcher: BrowserFetcher, monkeypatch: pytest.MonkeyPatch) -> None:
    result, events = run_with(fetcher, monkeypatch, [page()])

    assert (result.html, events) == ("<html>товар</html>", ["open"])


def test_blocked_profile_is_reset_and_retried_once(fetcher: BrowserFetcher, monkeypatch: pytest.MonkeyPatch) -> None:
    result, events = run_with(fetcher, monkeypatch, [page(498, ""), page(498, "")])

    assert events == ["open", "reset", "open"]
    assert result.status == 498  # вторая попытка — последняя; решение за адаптером


def test_not_ready_but_not_blocked_is_not_retried(fetcher: BrowserFetcher, monkeypatch: pytest.MonkeyPatch) -> None:
    _, events = run_with(fetcher, monkeypatch, [page(200, "<html>пусто</html>")])

    assert events == ["open"]
