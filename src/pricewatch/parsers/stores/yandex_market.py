"""Яндекс Маркет (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Карточка отдаётся быстрым запросом без браузера (06.10.2026); если Маркет покажет капчу или
закроет доступ — тот же разбор по странице из браузера. Регион Маркет выбирает по IP, cookie
`yandex_gid` не учитывает; выбранный регион лежит в данных страницы как
`"region":{"id":2,…,"name":"Санкт-Петербург"}` — по нему город и подтверждается.
Товар в продаже: JSON-LD Product — название, фото, наличие (общий парсер). Цена в JSON-LD — с картой
Яндекс Пэй (06.10.2026); обе цены лежат в блоке `priceDetails` парами «сумма — подпись»:
«Пэй» — цена по карте, «без карты» — обычная. Нет блока — цена JSON-LD с заметкой. Товара нет в продаже:
JSON-LD нет, в данных страницы `"isNotOnSale":true`; название — из h1.
Адрес приводится к /card/<название>/<id> без рекламных параметров (cpc, sponsored, do-waremd5…).
"""

import html as html_entities
import json
import re
from typing import Any

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, StoreBrowser, looks_blocked
from pricewatch.parsers.contract import TITLE_MAX, ParsedProduct
from pricewatch.parsers.prices import structured_price
from pricewatch.parsers.fetch import Fetcher
from pricewatch.parsers.generic import parse_html
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

KEY = "yandex_market"
CARD_PATH = re.compile(r"^/(card/[^/?#]+/\d{4,20}|product--[^/?#]+/\d{4,20})")
REGION = re.compile(r'"region":\{"id":(\d+),[^{}]*?"name":"([^"]{1,100})"')
REGION_IDS = {"spb": 2}
NOT_ON_SALE = '"isNotOnSale":true'
PRODUCT_JSON_LD = re.compile(r'<script type="application/ld\+json"[^>]*>[^<]*"@type":\s*"Product"')
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
PRICE_DETAILS = '"priceDetails":'
WITHOUT_CARD = "без карты"
PAY_NOTE = "цена может быть с картой Яндекс Пэй: блока с ценой без карты на странице нет"
TAG = re.compile(r"<[^>]+>")


class YandexMarketParser:
    key = KEY
    hosts = frozenset({"market.yandex.ru"})

    def __init__(self, fetcher: Fetcher, browser: StoreBrowser) -> None:
        self._fetcher = fetcher
        self._browser = browser

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        region = REGION_IDS.get(region_code)
        if region is None:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"region": region_code})
        card = canonical_url(url)
        response = self._fetcher.fetch(card)
        html = response.text() if response.status == 200 else ""
        if not _ready(html):
            # Капча или закрытый доступ для быстрого запроса — та же страница через браузер.
            page = self._browser.fetch(card, KEY, lambda page: _ready(page.html))
            if not _ready(page.html):
                raise _not_ready(page)
            html = page.html
        return product_from_page(html, card, region_code, region)


def canonical_url(url: NormalizedUrl) -> NormalizedUrl:
    match = CARD_PATH.match(url.url.split(url.host, 1)[1])
    if match is None:
        raise PriceWatcherError(ErrorCode.INVALID_URL)
    return normalize_url(f"https://market.yandex.ru/{match.group(1)}")


def selected_region(html: str) -> tuple[int, str]:
    match = REGION.search(html)
    if match is None:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_region"})
    return int(match.group(1)), match.group(2)


def product_from_page(html: str, card: NormalizedUrl, region_code: str, region: int) -> ParsedProduct:
    region_id, region_name = selected_region(html)
    if region_id != region:
        raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"region_id": region_id, "city": region_name})
    if PRODUCT_JSON_LD.search(html):
        product = parse_html(html, card, region_code)
        pairs = price_details(html)
        regular = next((price for price, label in pairs if WITHOUT_CARD in label.lower()), None)
        card_price = next((price for price, label in pairs if "пэй" in label.lower()), None)
        notes = product.notes
        if regular is None:
            regular, card_price, notes = product.price_minor, None, (*product.notes, PAY_NOTE)
        if card_price is not None and regular is not None and card_price >= regular:
            card_price = None
        return ParsedProduct(
            product.title, regular, card_price, product.availability, product.image_url,
            product.region_code, product.source, "Яндекс Маркет", notes,
        )
    if NOT_ON_SALE in html:
        return ParsedProduct(_title(html), None, None, Availability.OUT_OF_STOCK, None, region_code, site_name="Яндекс Маркет")
    raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_product_data"})


def price_details(html: str) -> list[tuple[int, str]]:
    """Пары «цена в копейках — подпись» из блока priceDetails страницы; нет блока — пусто."""
    start = html.find(PRICE_DETAILS)
    if start < 0:
        return []
    value_start = start + len(PRICE_DETAILS)
    while value_start < len(html) and html[value_start].isspace():
        value_start += 1  # raw_decode не пропускает пробелы перед значением
    try:
        details, _ = json.JSONDecoder().raw_decode(html, value_start)
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "price_details_json"}) from error
    pairs = []
    for entry in details.get("prices") or [] if isinstance(details, dict) else []:
        amount = _first_price(_content(entry.get("value")) if isinstance(entry, dict) else [])
        label = " ".join(
            part["value"].strip() for part in _content(entry.get("subtitle")) if part.get("type") == "literal" and str(part.get("value", "")).strip()
        )
        if amount is not None:
            pairs.append((amount, label))
    return pairs


def _content(block: Any) -> list[dict[str, Any]]:
    content = block.get("content") if isinstance(block, dict) else None
    return [part for part in content if isinstance(part, dict)] if isinstance(content, list) else []


def _first_price(parts: list[dict[str, Any]]) -> int | None:
    for part in parts:
        if part.get("type") == "price" and isinstance(part.get("value"), str):
            try:
                return structured_price(part["value"])  # «50117.000000» — нули после точки допустимы
            except ValueError as error:
                raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "price_value"}) from error
    return None


def _ready(html: str) -> bool:
    return bool(REGION.search(html)) and (bool(PRODUCT_JSON_LD.search(html)) or NOT_ON_SALE in html)


def _title(html: str) -> str | None:
    match = H1.search(html)
    if match is None:
        return None
    text = " ".join(html_entities.unescape(TAG.sub("", match.group(1))).split())
    return text[:TITLE_MAX] or None


def _not_ready(page: BrowserPage) -> PriceWatcherError:
    if "showcaptcha" in page.final_url:
        return PriceWatcherError(ErrorCode.CAPTCHA)
    if looks_blocked(page):
        return PriceWatcherError(ErrorCode.BLOCKED, {"status": page.status})
    if page.status in (404, 410):
        return PriceWatcherError(ErrorCode.PRICE_NOT_FOUND, {"status": page.status})
    return PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_product_data"})
