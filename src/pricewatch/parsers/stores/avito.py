"""Авито (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Самый строгий к автоматическим запросам магазин: только браузер (patchright) с профилем Авито,
в котором администратор хотя бы раз прошёл проверку вручную (06.10.2026 без этого — 429 и капча
с первого запроса, после ручной проверки — пропускает). Проверять редко и с паузами.
Объявление — JSON-LD Product (@graph) с ценой продавца: её разбирает общий парсер.
Цена объявления от региона смотрящего не зависит: её назначил продавец, поэтому регион не
подтверждается отдельно; город объявления виден в адресе (/sankt-peterburg/…).
Снятое объявление (06.10.2026): JSON-LD остаётся, в нём OutOfStock, а на странице — «Объявление снято
с публикации.». Надпись на странице важнее данных для поисковиков: объявление считается снятым.
"""

import html as html_entities
import re

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, StoreBrowser, looks_blocked
from pricewatch.parsers.contract import TITLE_MAX, ParsedProduct
from pricewatch.parsers.generic import parse_html
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

KEY = "avito"
LISTING_PATH = re.compile(r"^/[a-z_-]+/[a-z_]+/[a-z0-9_-]+_\d{6,15}")
PRODUCT_JSON_LD = re.compile(r'<script type="application/ld\+json"[^>]*>[^<]*"@type":\s*"Product"')
CLOSED_TEXTS = ("снято с публикации", "объявление закрыто")
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
TAG = re.compile(r"<[^>]+>")


class AvitoParser:
    key = KEY
    hosts = frozenset({"avito.ru"})

    def __init__(self, browser: StoreBrowser) -> None:
        self._browser = browser

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        listing = canonical_url(url)
        page = self._browser.fetch(listing, KEY, _loaded)
        html = page.html
        if _closed(html):
            # Сначала надпись: данные для поисковиков у снятого объявления могут отставать или быть без цены.
            return ParsedProduct(_title(html), None, None, Availability.OUT_OF_STOCK, None, region_code, site_name="Авито")
        if PRODUCT_JSON_LD.search(html):
            product = parse_html(html, listing, region_code)
            return ParsedProduct(
                product.title, product.price_minor, None, product.availability, product.image_url,
                region_code, product.source, "Авито", product.notes,
            )
        raise _not_loaded(page)


def canonical_url(url: NormalizedUrl) -> NormalizedUrl:
    """Адрес объявления без параметров: /<город>/<категория>/<название>_<номер>."""
    match = LISTING_PATH.match(url.url.split(url.host, 1)[1])
    if match is None:
        raise PriceWatcherError(ErrorCode.INVALID_URL)
    return normalize_url(f"https://www.avito.ru{match.group(0)}")


def _loaded(page: BrowserPage) -> bool:
    return bool(PRODUCT_JSON_LD.search(page.html)) or _closed(page.html)


def _closed(html: str) -> bool:
    lowered = html.lower()
    return any(text in lowered for text in CLOSED_TEXTS)


def _not_loaded(page: BrowserPage) -> PriceWatcherError:
    if page.status in (404, 410):
        return PriceWatcherError(ErrorCode.PRICE_NOT_FOUND, {"status": page.status})
    if page.status == 429 or looks_blocked(page):
        # Защита Авито: проверку нужно пройти вручную в профиле worker.
        return PriceWatcherError(ErrorCode.CAPTCHA, {"status": page.status})
    return PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_listing_data"})


def _title(html: str) -> str | None:
    match = H1.search(html)
    if match is None:
        return None
    return " ".join(html_entities.unescape(TAG.sub("", match.group(1))).split())[:TITLE_MAX] or None
