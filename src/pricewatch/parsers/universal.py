"""Общий парсер для любого сайта без своего адаптера (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Решение владельца (06.10.2026): ссылку на любой сайт добавляет любой пользователь, цена видна сразу.
Лестница способов: быстрый запрос → браузер. Начинаем с того, что сработал для сайта в прошлый раз.
Ответу быстрого запроса окончательно не верим: защита может отдать 404 или пустую страницу, поэтому
при любой его неудаче пробуем браузер. Ответ браузера — окончательный.

Источники цены по очереди: данные сайта (JSON-LD, meta, микроразметка) → цена «глазами».
Если есть и то и другое, а они расходятся больше чем на 3 %, это попадает в заметки.
"""

import re
from typing import Protocol

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, StoreBrowser, looks_blocked
from pricewatch.parsers.contract import IMAGE_URL_MAX, TITLE_MAX, ParsedProduct, PriceSource
from pricewatch.parsers.fetch import Fetcher
from pricewatch.parsers.generic import HTML_TYPES, check_status, parse_html
from pricewatch.parsers.urls import NormalizedUrl
from pricewatch.parsers.visual import VISUAL_JS, VisualPrice, availability, pick_price

HTTP = "http"
BROWSER = "browser"
LADDER = (HTTP, BROWSER)
MISMATCH_PERCENT = 3
# Ошибки, при которых браузер не поможет: адрес плохой или цена точно не в рублях.
FINAL_CODES = frozenset({ErrorCode.INVALID_URL, ErrorCode.UNSUPPORTED_CURRENCY})
PROFILE_UNSAFE = re.compile(r"[^a-z0-9.-]")


class MethodMemory(Protocol):
    """Какой способ сработал для сайта; хранение — в базе на этапе 6."""

    def get(self, store_host: str) -> str | None: ...

    def remember(self, store_host: str, method: str) -> None: ...


class InMemoryMethods:
    def __init__(self) -> None:
        self._methods: dict[str, str] = {}

    def get(self, store_host: str) -> str | None:
        return self._methods.get(store_host)

    def remember(self, store_host: str, method: str) -> None:
        self._methods[store_host] = method


class UniversalParser:
    key = "universal"

    def __init__(self, fetcher: Fetcher, browser: StoreBrowser, methods: MethodMemory) -> None:
        self._fetcher = fetcher
        self._browser = browser
        self._methods = methods

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        remembered = self._methods.get(url.store_host)
        order = [remembered, *(method for method in LADDER if method != remembered)] if remembered in LADDER else list(LADDER)
        failure: PriceWatcherError | None = None
        for method in order:
            try:
                product = self._via_http(url, region_code) if method == HTTP else self._via_browser(url, region_code)
            except PriceWatcherError as error:
                if error.code in FINAL_CODES or method == BROWSER:
                    raise
                failure = error
                continue
            self._methods.remember(url.store_host, method)
            return product
        raise failure if failure is not None else PriceWatcherError(ErrorCode.PRICE_NOT_FOUND)

    def _via_http(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        response = self._fetcher.fetch(url)
        check_status(response)
        if response.content_type.split(";")[0].strip().lower() not in HTML_TYPES:
            raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "not_html"})
        return parse_html(response.text(), response.url, region_code)

    def _via_browser(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        page = self._browser.fetch(url, profile_key(url.store_host), lambda page: _found(page, region_code), probe_js=VISUAL_JS)
        return product_from_page(page, region_code)


def profile_key(store_host: str) -> str:
    """Имя папки профиля браузера для сайта: только безопасные символы."""
    return "site-" + PROFILE_UNSAFE.sub("_", store_host)


def product_from_page(page: BrowserPage, region_code: str) -> ParsedProduct:
    """Данные сайта, а если их нет — цена «глазами»; иначе понятная ошибка."""
    visual = pick_price(page.probe)
    try:
        structured = parse_html(page.html, page.url, region_code) if page.html else None
    except PriceWatcherError as error:
        if error.code is not ErrorCode.PRICE_NOT_FOUND:
            raise
        structured = None
    if structured is not None:
        return _with_check(structured, visual)
    if visual is not None:
        return _from_visual(page, visual, region_code)
    if looks_blocked(page):
        raise PriceWatcherError(ErrorCode.BLOCKED, {"status": page.status})
    if page.status in (404, 410):
        raise PriceWatcherError(ErrorCode.PRICE_NOT_FOUND, {"status": page.status})
    if availability(page.probe) is Availability.OUT_OF_STOCK:
        return ParsedProduct(_title(page), None, None, Availability.OUT_OF_STOCK, _image(page), region_code, PriceSource.PAGE)
    raise PriceWatcherError(ErrorCode.PRICE_NOT_FOUND)


def _found(page: BrowserPage, region_code: str) -> bool:
    # Ждём, пока на странице появится товар: данные сайта (любой ответ, кроме «цены нет») или ценник.
    if pick_price(page.probe) is not None:
        return True
    try:
        parse_html(page.html, page.url, region_code)
    except PriceWatcherError as error:
        return error.code is not ErrorCode.PRICE_NOT_FOUND
    return True


def _with_check(product: ParsedProduct, visual: VisualPrice | None) -> ParsedProduct:
    if product.price_minor is None or visual is None:
        return product
    if abs(visual.price_minor - product.price_minor) * 100 <= MISMATCH_PERCENT * product.price_minor:
        return product
    note = f"на странице крупно другая цена: {visual.price_minor} коп. (в данных сайта {product.price_minor} коп.)"
    return ParsedProduct(
        product.title, product.price_minor, product.card_price_minor, product.availability, product.image_url,
        product.region_code, product.source, product.site_name, (*product.notes, note),
    )


def _from_visual(page: BrowserPage, visual: VisualPrice, region_code: str) -> ParsedProduct:
    found = availability(page.probe)
    notes = () if found is not Availability.UNKNOWN else ("наличие сайт явно не указал — считаю, что есть",)
    return ParsedProduct(_title(page), visual.price_minor, None, found, _image(page), region_code, PriceSource.PAGE, None, notes)


def _title(page: BrowserPage) -> str | None:
    probe = page.probe if isinstance(page.probe, dict) else {}
    heading = probe.get("h1")
    if isinstance(heading, str) and heading.strip():
        return " ".join(heading.split())[:TITLE_MAX]
    return None


def _image(page: BrowserPage) -> str | None:
    probe = page.probe if isinstance(page.probe, dict) else {}
    image = probe.get("image")
    if isinstance(image, str) and image.startswith("https://") and len(image) <= IMAGE_URL_MAX:
        return image
    return None
