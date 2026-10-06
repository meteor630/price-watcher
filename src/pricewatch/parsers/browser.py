"""Браузерная загрузка для магазинов за защитой от ботов (DEVELOPMENT_ARCHITECTURE.md, раздел 7, ступень 3).

Решение владельца — patchright (github.com/Kaliiiiiiiiii-Vinyzu/patchright-python, Apache-2.0):
Playwright, у которого закрыты признаки управляемого браузера. Обычный Playwright и обычный Chrome
защиты Wildberries, Ozon и DNS не пропускают (проверено 06.10.2026). Способ запуска взят из
рабочего парсера владельца и переписан под правила проекта:

- установленный Google Chrome (`channel="chrome"`) с окном: окно за краем экрана, на сервере —
  виртуальный экран Xvfb; язык — флагом Chrome, часовой пояс системный, ничего не подменяется;
- у каждого магазина свой постоянный профиль: cookies, выданные защитой, переживают перезапуск;
  если магазин пометил профиль как робота, профиль стирается и делается вторая попытка;
- ждём не загрузку, а результат: защита сначала показывает проверку и перезагружает страницу;
- JSON, который страница запрашивает сама, сохраняется — его читают адаптеры;
- картинки отключены настройкой Chrome: цене они не нужны.

Защита от SSRF: Chrome ходит в сеть только через проверяющий прокси `EgressProxy` — документ,
подресурсы, перенаправления, всплывающие окна, WebSocket. QUIC и прямой UDP у WebRTC отключены,
иначе Chrome мог бы соединяться мимо прокси. Адрес самой страницы проверяется до открытия.
"""

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from patchright.sync_api import BrowserContext, Error, Page, Playwright, Response, sync_playwright

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.egress import EgressProxy, public_address
from pricewatch.parsers.fetch import Resolver, system_resolver
from pricewatch.parsers.urls import NormalizedUrl

CHROME_ARGS = (
    "--lang=ru-RU",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1366,900",
    "--window-position=-2400,-2400",
    "--blink-settings=imagesEnabled=false",
    "--autoplay-policy=user-gesture-required",
    # Всё — через прокси, включая петлевые адреса (по умолчанию Chrome отправляет их напрямую).
    "--proxy-bypass-list=<-loopback>",
    "--disable-quic",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)
NAVIGATION_TIMEOUT_MS = 45_000
WAIT_STEP_MS = 400
CAPTURED_MAX_COUNT = 200
CAPTURED_MAX_BYTES = 3_000_000
BLOCK_MARKERS = (
    "captcha", "капча", "qrator", "доступ ограничен", "access denied", "are you a robot", "checking your browser",
    "проверка браузера", "вы не робот", "antibot", "ddos-guard", "servicepipe", "variti", "почти готово",
)


@dataclass
class BrowserPage:
    url: NormalizedUrl
    final_url: str
    status: int
    html: str
    # JSON-ответы, которые страница получила сама: (адрес, тело).
    json: list[tuple[str, str]] = field(default_factory=list)


Ready = Callable[[BrowserPage], bool]


def looks_blocked(page: BrowserPage) -> bool:
    """Заглушка защиты вместо товара: код ответа или характерные слова в начале страницы."""
    if page.status in (401, 403, 429, 498, 503):
        return True
    head = page.html[:40_000].lower()
    return any(marker in head for marker in BLOCK_MARKERS)


def _snapshot(tab: Page) -> str | None:
    """HTML страницы или None, если она как раз перезагружается после проверки защиты."""
    try:
        return tab.content()
    except Error:
        return None


class BrowserFetcher:
    """Один Chrome за раз; профиль переключается на магазин перед загрузкой."""

    def __init__(self, profiles_dir: Path, *, resolver: Resolver = system_resolver) -> None:
        self._profiles_dir = profiles_dir
        self._resolver = resolver
        self._proxy = EgressProxy(resolver=resolver)
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._profile: Path | None = None

    def close(self) -> None:
        self._close_browser()
        self._proxy.close()

    def fetch(self, url: NormalizedUrl, store_key: str, ready: Ready, timeout_s: float = 25.0) -> BrowserPage:
        """Открыть страницу в профиле магазина и дождаться `ready` или конца времени.

        Если товар не появился, а страница похожа на заглушку защиты, профиль магазина стирается
        и делается вторая, последняя попытка.
        """
        if public_address(url.host, self._resolver) is None:
            raise PriceWatcherError(ErrorCode.INVALID_URL)
        page = self._open(url, store_key, ready, timeout_s)
        if ready(page) or not looks_blocked(page):
            return page
        self._reset_profile()
        return self._open(url, store_key, ready, timeout_s)

    def context(self, store_key: str) -> BrowserContext:
        """Chrome с профилем магазина; запускается при первом обращении."""
        profile = self._profiles_dir / store_key
        if self._context is not None and profile == self._profile:
            return self._context
        self._close_browser()
        self._profile = profile
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=False,
            args=[*CHROME_ARGS, f"--proxy-server=http://127.0.0.1:{self._proxy.port}"],
            no_viewport=True,
        )
        return self._context

    def _open(self, url: NormalizedUrl, store_key: str, ready: Ready, timeout_s: float) -> BrowserPage:
        tab = self.context(store_key).new_page()
        result = BrowserPage(url, url.url, 0, "")
        tab.on("response", lambda response: _record(tab, response, result))
        deadline = time.monotonic() + timeout_s
        try:
            tab.goto(url.url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            while True:
                html = _snapshot(tab)
                if html is not None:
                    result.html = html
                result.final_url = tab.url
                if (result.html and ready(result)) or time.monotonic() > deadline:
                    break
                tab.wait_for_timeout(WAIT_STEP_MS)
        except Error as error:
            raise PriceWatcherError(ErrorCode.TIMEOUT, {"reason": "browser_failed"}) from error
        finally:
            tab.close()
        return result

    def _close_browser(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _reset_profile(self) -> None:
        """Стереть профиль текущего магазина вместе с меткой «робот»."""
        profile = self._profile
        self._close_browser()
        self._profile = None
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)


def _record(tab: Page, response: Response, result: BrowserPage) -> None:
    request = response.request
    if request.is_navigation_request() and response.frame == tab.main_frame:
        result.status = response.status
        return
    content_type = response.headers.get("content-type") or ""
    if request.resource_type in ("xhr", "fetch") and "json" in content_type and len(result.json) < CAPTURED_MAX_COUNT:
        try:
            body = response.text()
        except Error:
            return  # ответ уже недоступен (страница ушла дальше) — адаптер обойдётся остальными
        if len(body) <= CAPTURED_MAX_BYTES:
            result.json.append((response.url, body))
