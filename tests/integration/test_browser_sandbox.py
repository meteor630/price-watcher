"""Chrome worker работает в своей песочнице: страницы магазинов — недоверенный контент (решение владельца 12.10.2026).

Playwright по умолчанию запускает Chrome с --no-sandbox; worker обязан просить песочницу явно.
Нужен установленный Google Chrome и экран (на Linux в CI — xvfb-run).
"""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pricewatch.parsers.browser import BrowserFetcher


@pytest.fixture
def browser(tmp_path: Path) -> Iterator[BrowserFetcher]:
    fetcher = BrowserFetcher(tmp_path / "profiles", resolver=lambda host: [])
    yield fetcher
    fetcher.close()


def test_chrome_runs_without_no_sandbox_flag(browser: BrowserFetcher) -> None:
    tab = browser.context("sandbox-check").new_page()
    tab.goto("chrome://version")
    command_line = tab.inner_text("#command_line")
    tab.close()

    assert "--no-sandbox" not in command_line


@pytest.mark.skipif(sys.platform != "linux", reason="страница состояния песочницы есть только в Chrome для Linux")
def test_chrome_sandbox_is_adequate_on_linux(browser: BrowserFetcher) -> None:
    tab = browser.context("sandbox-check").new_page()
    tab.goto("chrome://sandbox")
    status = tab.inner_text("body")
    tab.close()

    assert "You are adequately sandboxed" in status, status
