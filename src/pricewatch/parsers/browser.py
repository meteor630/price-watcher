"""Браузерная загрузка для магазинов за защитой от ботов (DEVELOPMENT_ARCHITECTURE.md, раздел 7, ступень 3).

Решение владельца — patchright (github.com/Kaliiiiiiiiii-Vinyzu/patchright-python, Apache-2.0):
Playwright, у которого закрыты признаки управляемого браузера. Обычный Playwright и обычный Chrome
защиты Wildberries, Ozon и DNS не пропускают (проверено 06.10.2026). Способ запуска взят из
рабочего парсера владельца и переписан под правила проекта:

- установленный Google Chrome (`channel="chrome"`) с окном: окно за краем экрана, на Linux без монитора —
  виртуальный экран Xvfb; язык — флагом Chrome, часовой пояс системный, ничего не подменяется;
- у каждого магазина свой постоянный профиль: cookies, выданные защитой, переживают перезапуск;
  если магазин пометил профиль как робота, профиль стирается и делается вторая попытка;
- ждём не загрузку, а результат: защита сначала показывает проверку и перезагружает страницу;
- JSON, который страница запрашивает сама, сохраняется — его читают адаптеры; адаптер может и сам
  запросить JSON изнутри открытой страницы (`open` → `fetch_json`) — с cookies, выданными защитой магазина;
- картинки отключены настройкой Chrome: цене они не нужны.

Защита от SSRF: Chrome ходит в сеть только через проверяющий прокси `EgressProxy` — документ,
подресурсы, перенаправления, всплывающие окна, WebSocket. QUIC и прямой UDP у WebRTC отключены,
иначе Chrome мог бы соединяться мимо прокси. Адрес самой страницы проверяется до открытия.
"""

import shutil
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

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
MAX_OPEN_PROFILES = 2
WAIT_STEP_MS = 400
CAPTURED_MAX_COUNT = 200
CAPTURED_MAX_BYTES = 3_000_000
BLOCK_MARKERS = (
    "captcha", "капча", "qrator", "доступ ограничен", "access denied", "are you a robot", "checking your browser",
    "проверка браузера", "вы не робот", "antibot", "ddos-guard", "servicepipe", "variti", "почти готово",
)


# Заголовки запросов страницы не сохраняются целиком: cookies и авторизация — секреты сессии.
SECRET_HEADERS = frozenset({"cookie", "authorization", "proxy-authorization"})


@dataclass(frozen=True, slots=True)
class CapturedJson:
    """JSON, который страница запросила сама: адрес, тело и заголовки её запроса (без cookies)."""

    url: str
    body: str
    request_headers: dict[str, str]


@dataclass
class BrowserPage:
    url: NormalizedUrl
    final_url: str
    status: int
    html: str
    json: list[CapturedJson] = field(default_factory=list)
    # Результат скрипта-зонда (например, VISUAL_JS) на последнем опросе страницы.
    probe: Any = None


Ready = Callable[[BrowserPage], bool]

# fetch() изнутри страницы: тот же источник, cookies магазина. Тело больше предела не передаётся.
FETCH_JSON_JS = """async ([urls, headers, maxBytes]) => Promise.all(urls.map(async (url) => {
    try {
        const response = await fetch(url, {credentials: "include", headers: headers});
        const text = await response.text();
        return [response.status, text.length > maxBytes ? null : text];
    } catch (error) {
        return [0, null];
    }
}))"""


@dataclass(frozen=True, slots=True)
class JsonReply:
    status: int  # 0 — запрос не дошёл (сеть, прокси отказал)
    body: str | None  # None — нет ответа или он больше предела


def _probe(tab: Page, script: str, previous: Any) -> Any:
    """Результат зонда или прошлый, если страница как раз перезагружается."""
    try:
        return tab.evaluate(script)
    except Error:
        return previous


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


class OpenPage:
    """Открытая страница магазина: из неё можно запрашивать JSON с cookies магазина. Закрывается `with`."""

    def __init__(self, page: BrowserPage, tab: Page | None) -> None:
        self.page = page
        self._tab = tab

    def __enter__(self) -> "OpenPage":
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._tab is not None:
            self._tab.close()
            self._tab = None

    def fetch_json(self, requests: list[NormalizedUrl], headers: dict[str, str] | None = None) -> list[JsonReply]:
        """Запросы изнутри страницы — тот же источник, cookies магазина, тот же проверяющий прокси.

        `headers` — обычно заголовки, которые сайт сам ставит своим запросам (см. CapturedJson).
        """
        if self._tab is None:
            raise RuntimeError("страница уже закрыта")
        try:
            raw = self._tab.evaluate(
                FETCH_JSON_JS, [[request.url for request in requests], headers or {"accept": "application/json"}, CAPTURED_MAX_BYTES]
            )
        except Error as error:
            raise PriceWatcherError(ErrorCode.TIMEOUT, {"reason": "browser_failed"}) from error
        return [JsonReply(int(status), body) for status, body in raw]


class StoreBrowser(Protocol):
    """То, что нужно адаптеру от браузера; рабочая реализация — BrowserFetcher."""

    def open(
        self, url: NormalizedUrl, store_key: str, ready: Ready, timeout_s: float = ..., probe_js: str | None = ...
    ) -> OpenPage: ...


class BrowserFetcher:
    """Повторно использует два недавно открытых профиля магазинов в одном процессе Playwright."""

    def __init__(self, profiles_dir: Path, *, resolver: Resolver = system_resolver) -> None:
        self._profiles_dir = profiles_dir
        self._resolver = resolver
        self._proxy = EgressProxy(resolver=resolver)
        self._playwright: Playwright | None = None
        self._contexts: OrderedDict[Path, BrowserContext] = OrderedDict()

    def close(self) -> None:
        try:
            for context in self._contexts.values():
                context.close()
        finally:
            self._contexts.clear()
            try:
                if self._playwright is not None:
                    self._playwright.stop()
                    self._playwright = None
            finally:
                self._proxy.close()

    def fetch(
        self, url: NormalizedUrl, store_key: str, ready: Ready, timeout_s: float = 25.0, probe_js: str | None = None
    ) -> BrowserPage:
        """Открыть страницу в профиле магазина и дождаться `ready` или конца времени.

        Если товар не появился, а страница похожа на заглушку защиты, профиль магазина стирается
        и делается вторая, последняя попытка.
        """
        with self.open(url, store_key, ready, timeout_s, probe_js) as opened:
            return opened.page

    def open(
        self, url: NormalizedUrl, store_key: str, ready: Ready, timeout_s: float = 25.0, probe_js: str | None = None
    ) -> OpenPage:
        """Как `fetch`, но страница остаётся открытой для запросов JSON изнутри неё.

        `probe_js` выполняется в странице на каждом опросе; результат — в `BrowserPage.probe`.
        """
        page, tab = self._load(url, store_key, ready, timeout_s, probe_js)
        return OpenPage(page, tab)

    def _load(
        self, url: NormalizedUrl, store_key: str, ready: Ready, timeout_s: float, probe_js: str | None
    ) -> tuple[BrowserPage, Page]:
        if public_address(url.host, self._resolver) is None:
            raise PriceWatcherError(ErrorCode.INVALID_URL)
        page, tab = self._open(url, store_key, ready, timeout_s, probe_js)
        if ready(page) or not looks_blocked(page):
            return page, tab
        tab.close()
        self._reset_profile(store_key)
        return self._open(url, store_key, ready, timeout_s, probe_js)

    def context(self, store_key: str) -> BrowserContext:
        """Chrome с профилем магазина; запускается при первом обращении."""
        profile = self._profiles_dir / store_key
        active = self._contexts.pop(profile, None)
        if active is not None and (active.browser is None or active.browser.is_connected()):
            self._contexts[profile] = active
            return active
        if len(self._contexts) >= MAX_OPEN_PROFILES:
            _, oldest = self._contexts.popitem(last=False)
            oldest.close()
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        context = self._playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=False,
            # Песочница Chrome: страницы магазинов — недоверенный контент; Playwright без этого добавляет --no-sandbox.
            chromium_sandbox=True,
            args=[*CHROME_ARGS, f"--proxy-server=http://127.0.0.1:{self._proxy.port}"],
            no_viewport=True,
            accept_downloads=False,  # сайты пользователей не могут ничего скачать на worker
        )
        self._contexts[profile] = context
        return context

    def _open(
        self, url: NormalizedUrl, store_key: str, ready: Ready, timeout_s: float, probe_js: str | None
    ) -> tuple[BrowserPage, Page]:
        """Загрузить страницу и дождаться результата; вкладку закрывает вызывающий."""
        tab = self.context(store_key).new_page()
        result = BrowserPage(url, url.url, 0, "")
        tab.on("response", lambda response: _record(tab, response, result))
        deadline = time.monotonic() + timeout_s
        try:
            navigation_ms = max(1, min(NAVIGATION_TIMEOUT_MS, int(timeout_s * 1000)))
            tab.goto(url.url, wait_until="domcontentloaded", timeout=navigation_ms)
            while True:
                html = _snapshot(tab)
                if html is not None:
                    result.html = html
                if probe_js is not None:
                    result.probe = _probe(tab, probe_js, result.probe)
                result.final_url = tab.url
                if (result.html and ready(result)) or time.monotonic() > deadline:
                    break
                tab.wait_for_timeout(WAIT_STEP_MS)
        except Error as error:
            tab.close()
            raise PriceWatcherError(ErrorCode.TIMEOUT, {"reason": "browser_failed"}) from error
        return result, tab

    def _reset_profile(self, store_key: str) -> None:
        """Стереть профиль магазина, не останавливая браузеры других магазинов."""
        profile = self._profiles_dir / store_key
        context = self._contexts.pop(profile, None)
        if context is not None:
            context.close()
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
            headers = request.all_headers()
        except Error:
            return  # ответ уже недоступен (страница ушла дальше) — адаптер обойдётся остальными
        if len(body) <= CAPTURED_MAX_BYTES:
            safe = {name: value for name, value in headers.items() if name.lower() not in SECRET_HEADERS and not name.startswith(":")}
            result.json.append(CapturedJson(response.url, body, safe))
