"""DNS (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Сайт закрыт защитой Qrator: только браузер (patchright, профиль магазина). Город DNS выбирает
по IP и хранит в подписанной cookie, подделать её нельзя; выбранный город страница кладёт в свои
настройки как `cityGuid` — по нему город и подтверждается. Все данные — из JSON, который страница
запрашивает сама:
- название и фото — `/product/microdata/<id>/`, запись schema.org Product (её разбирает та же логика, что JSON-LD);
- цена — блок кнопки «Купить» `/ajax-state/product-buy/` (`price.current`): её видит покупатель. В microdata цена
  бывает другой (13.10.2026: 33 799 ₽ против 31 799 ₽ на кнопке);
- наличие — товар можно положить в корзину: в блоке способов получения `/ajax-state/avails-container/` есть
  `data-cart-available` (магазин, пункт выдачи или доставка), а кнопка не говорит `notAvail`. Отметка OutOfStock
  в microdata означает только «нет в магазинах города» и для наличия не годится: товар с доставкой она помечала
  отсутствующим (ошибка 13.10.2026).
"""

import json
import re
from dataclasses import replace
from typing import Any

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, CapturedJson, StoreBrowser, looks_blocked
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.generic import product_from_json_ld
from pricewatch.parsers.prices import structured_price
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

KEY = "dns"
PRODUCT_PATH = re.compile(r"^/product/[0-9a-f]{12,32}/[^?#]*")
MICRODATA_PATH = "/product/microdata/"
BUY_PATH = "/ajax-state/product-buy/"
AVAILS_PATH = "/ajax-state/avails-container/"
RESPONSES = (MICRODATA_PATH, BUY_PATH, AVAILS_PATH)
CART_AVAILABLE = "data-cart-available"
CITY_GUID = re.compile(r'"cityGuid":"([0-9a-f-]{36})"')
# Город DNS по региону проекта (guid из настроек страницы, 06.10.2026).
REGION_CITIES = {"spb": "566ca284-5bea-11e2-aee1-00155d030b1f"}


class DnsParser:
    key = KEY
    hosts = frozenset({"dns-shop.ru"})

    def __init__(self, browser: StoreBrowser) -> None:
        self._browser = browser

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        city = REGION_CITIES.get(region_code)
        if city is None:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"region": region_code})
        page_url = canonical_url(url)
        page = self._browser.fetch(page_url, KEY, lambda page: all(_captured(page, path) is not None for path in RESPONSES))
        microdata, buy, avails = (_captured(page, path) for path in RESPONSES)
        if microdata is None:
            raise PriceWatcherError(ErrorCode.BLOCKED if looks_blocked(page) else ErrorCode.LAYOUT_CHANGED)
        page_city = selected_city(page.html)
        if page_city != city:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"city": page_city})
        if buy is None or avails is None:
            raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_buy_block"})
        return product_from_responses(microdata.body, buy.body, avails.body, page_url, region_code)


def canonical_url(url: NormalizedUrl) -> NormalizedUrl:
    """Адрес карточки без параметров: /product/<id>/<название>/."""
    match = PRODUCT_PATH.match(url.url.split(url.host, 1)[1])
    if match is None:
        raise PriceWatcherError(ErrorCode.INVALID_URL)
    return normalize_url(f"https://www.dns-shop.ru{match.group(0)}")


def selected_city(html: str) -> str:
    match = CITY_GUID.search(html)
    if match is None:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_city"})
    return match.group(1)


def product_from_microdata(body: str, page_url: NormalizedUrl, region_code: str) -> ParsedProduct:
    try:
        data = json.loads(body, parse_float=lambda text: text)  # дроби — строкой, разбираются без float
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "not_json"}) from error
    if not isinstance(data, dict) or data.get("result") is not True or not isinstance(data.get("data"), dict):
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "microdata_format"})
    return product_from_json_ld(data["data"], page_url, region_code, site_name="DNS")


def product_from_responses(microdata: str, buy: str, avails: str, page_url: NormalizedUrl, region_code: str) -> ParsedProduct:
    card = product_from_microdata(microdata, page_url, region_code)
    price, not_available = _buy_state(buy)
    if not_available or not _cart_available(avails):
        return replace(card, price_minor=None, card_price_minor=None, availability=Availability.OUT_OF_STOCK)
    return replace(card, price_minor=price, card_price_minor=None, availability=Availability.IN_STOCK)


def _buy_state(body: str) -> tuple[int, bool]:
    """Цена на кнопке «Купить» в копейках и отметка «нет в наличии» (тогда это последняя цена продажи)."""
    state = _first_state(body, "buy_state")
    price = state.get("price")
    try:
        minor = structured_price(price.get("current") if isinstance(price, dict) else None)
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "buy_price"}) from error
    return minor, state.get("notAvail") is True


def _cart_available(body: str) -> bool:
    html = _first_state(body, "avails").get("html")
    if not isinstance(html, str):
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "avails"})
    return CART_AVAILABLE in html


def _first_state(body: str, reason: str) -> dict[str, Any]:
    """Данные первого блока ответа ajax-state: {"result": true, "data": {"states": [{"data": {…}}]}}."""
    try:
        data = json.loads(body, parse_float=lambda text: text)  # дроби — строкой, разбираются без float
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": reason}) from error
    if not isinstance(data, dict) or data.get("result") is not True or not isinstance(data.get("data"), dict):
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": reason})
    states = data["data"].get("states")
    if not isinstance(states, list) or not states or not isinstance(states[0], dict) or not isinstance(states[0].get("data"), dict):
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": reason})
    return states[0]["data"]


def _captured(page: BrowserPage, path: str) -> CapturedJson | None:
    return next((captured for captured in page.json if path in captured.url), None)
