"""Ozon (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Сайт закрыт защитой: только браузер. Данных для поисковиков (JSON-LD) на карточке нет (06.10.2026),
поэтому цены берутся из виджета цены `webPrice`, где они записаны парами «сумма — подпись»:
«6 466 ₽ — С банками» (картой Ozon Банка и банков-партнёров) и «7 185 ₽ — С другими банками».
Первая — цена по карте, вторая — обычная. Если подпись одна или её нет, сумма — обычная цена.
Город Ozon выбирает сам и кладёт в настройки страницы: `"location":{"current":{…"city":…}}`.
Название — из h1 или og:title, фото — og:image. Наличие: есть цена и кнопка «В корзину» — в наличии;
нет виджета цены и есть «Товар закончился» — нет в наличии.
"""

import html as html_entities
import re

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, StoreBrowser, looks_blocked
from pricewatch.parsers.contract import IMAGE_URL_MAX, TITLE_MAX, ParsedProduct
from pricewatch.parsers.prices import structured_price
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

KEY = "ozon"
PRODUCT_PATH = re.compile(r"^/product/[a-z0-9-]*?(\d{4,15})/?")
REGION_CITIES = {"spb": "Санкт-Петербург"}
PRICE_WIDGET = 'data-widget="webPrice"'
CART_WIDGET = 'data-widget="webAddToCart"'
OUT_OF_STOCK_TEXTS = ("товар закончился", "этот товар закончился", "нет в наличии")
LOCATION_CITY = re.compile(r'"location":\{"current":\{[^{}]*?"city":"([^"]{1,100})"')
TAG = re.compile(r"<[^>]+>")
AMOUNT = re.compile(r"^(\d{1,3}(?:[    ]\d{3})*|\d+)(?:[.,]\d{1,2})?\s*₽$")
OTHER_BANKS = "другими банками"
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
META = re.compile(r'<meta[^>]+property="(og:[a-z_]+)"[^>]+content="([^"]*)"')


class OzonParser:
    key = KEY
    hosts = frozenset({"ozon.ru"})

    def __init__(self, browser: StoreBrowser) -> None:
        self._browser = browser

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        city = REGION_CITIES.get(region_code)
        if city is None:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"region": region_code})
        page = self._browser.fetch(canonical_url(url), KEY, _loaded)
        if not _loaded(page):
            raise PriceWatcherError(ErrorCode.BLOCKED if looks_blocked(page) else ErrorCode.LAYOUT_CHANGED)
        page_city = selected_city(page.html)
        if page_city != city:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"city": page_city})
        return product_from_page(page.html, region_code)


def canonical_url(url: NormalizedUrl) -> NormalizedUrl:
    """Адрес карточки без параметров: /product/<название>-<артикул>/."""
    match = PRODUCT_PATH.match(url.url.split(url.host, 1)[1])
    if match is None:
        raise PriceWatcherError(ErrorCode.INVALID_URL)
    return normalize_url(f"https://www.ozon.ru{match.group(0).rstrip('/')}/")


def selected_city(html: str) -> str:
    match = LOCATION_CITY.search(html)
    if match is None:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_location"})
    return match.group(1)


def price_pairs(html: str) -> list[tuple[int, str]]:
    """Суммы виджета цены по порядку и подпись после каждой (пустая, если подписи нет)."""
    start = html.find(PRICE_WIDGET)
    if start < 0:
        return []
    end = html.find("data-widget=", start + len(PRICE_WIDGET))
    tokens = [" ".join(html_entities.unescape(piece).split()) for piece in TAG.split(html[start: end if end > 0 else None])]
    tokens = [token for token in tokens if token]
    pairs: list[tuple[int, str]] = []
    for index, token in enumerate(tokens):
        match = AMOUNT.match(token)
        if match is None:
            continue
        label = tokens[index + 1] if index + 1 < len(tokens) and not AMOUNT.match(tokens[index + 1]) else ""
        pairs.append((structured_price(token.removesuffix("₽").strip()), label))
    return pairs


def product_from_page(html: str, region_code: str) -> ParsedProduct:
    meta = {name: html_entities.unescape(value) for name, value in META.findall(html)}
    title = _title(html, meta)
    image = meta.get("og:image") if meta.get("og:image", "").startswith("https://") and len(meta["og:image"]) <= IMAGE_URL_MAX else None
    pairs = price_pairs(html)
    if not pairs:
        if any(text in html.lower() for text in OUT_OF_STOCK_TEXTS):
            return ParsedProduct(title, None, None, Availability.OUT_OF_STOCK, image, region_code, site_name="Ozon")
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_price_widget"})
    regular = next((price for price, label in pairs if OTHER_BANKS in label.lower()), None)
    card = next((price for price, label in pairs if label and OTHER_BANKS not in label.lower()), None)
    if regular is None:
        # Одна цена без «других банков» — она и есть обычная, цены по карте нет.
        regular, card = pairs[0][0], None
    if card is not None and card >= regular:
        card = None
    availability = Availability.IN_STOCK if CART_WIDGET in html else Availability.UNKNOWN
    return ParsedProduct(title, regular, card, availability, image, region_code, site_name="Ozon")


def _loaded(page: BrowserPage) -> bool:
    lowered = page.html.lower()
    return PRICE_WIDGET in page.html or ('"location":{"current"' in page.html and any(t in lowered for t in OUT_OF_STOCK_TEXTS))


def _title(html: str, meta: dict[str, str]) -> str | None:
    match = H1.search(html)
    raw = html_entities.unescape(TAG.sub("", match.group(1))) if match else meta.get("og:title", "")
    text = " ".join(raw.split())
    return text[:TITLE_MAX] or None
