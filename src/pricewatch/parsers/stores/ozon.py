"""Ozon (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Сайт закрыт защитой: только браузер. Данных для поисковиков (JSON-LD) на карточке нет (06.10.2026),
поэтому цены берутся из виджета цены `webPrice`, где они записаны парами «сумма — подпись»:
«6 466 ₽ — С банками» (картой Ozon Банка и банков-партнёров) и «7 185 ₽ — С другими банками».
Первая — цена по карте, вторая — обычная. Если подпись одна или её нет, сумма — обычная цена.
Город Ozon выбирает сам и кладёт в настройки страницы: `"location":{"current":{…"city":…}}`.
Название — из h1 или og:title, фото — og:image. Наличие: есть цена и кнопка «В корзину» — в наличии.
Закончившийся товар Ozon перенаправляет на страницу поиска с виджетом `webOutOfStock` (06.10.2026):
там миниатюра, последняя цена серым и название товара. Название и фото берутся из виджета,
а не из страницы поиска (её заголовок — поисковый запрос, фото — логотип Ozon); цена — пустая.
Ссылка из поиска или каталога с `product_id` ведёт на карточку этого товара.
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
# Артикул — последнее число адреса карточки: в названии тоже бывают числа («krivizna-1800r», «kabel-2000»).
PRODUCT_PATH = re.compile(r"^/product/(?:[a-z0-9-]*-)?(\d{4,15})(?=/|\?|$)")
PRODUCT_ID_PARAM = re.compile(r"[?&]product_id=(\d{4,15})(?:&|$)")
OUT_OF_STOCK_WIDGET = 'data-widget="webOutOfStock"'
IMG_SRC = re.compile(r'<img[^>]+src="(https://[^"]+)"')
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
        on_card = "/product/" in page.final_url.split("?", 1)[0]
        return product_from_page(page.html, region_code, on_card=on_card)


def canonical_url(url: NormalizedUrl) -> NormalizedUrl:
    """Адрес карточки без параметров: /product/<название>-<артикул>/ или /product/<артикул>/ из product_id."""
    path = url.url.split(url.host, 1)[1]
    match = PRODUCT_PATH.match(path)
    if match is not None:
        return normalize_url(f"https://www.ozon.ru{match.group(0).rstrip('/')}/")
    by_id = PRODUCT_ID_PARAM.search(path)
    if by_id is not None:
        return normalize_url(f"https://www.ozon.ru/product/{by_id.group(1)}/")
    raise PriceWatcherError(ErrorCode.INVALID_URL)


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


def product_from_page(html: str, region_code: str, *, on_card: bool = True) -> ParsedProduct:
    """`on_card=False` — Ozon перенаправил на поиск: заголовок и фото страницы чужие, берутся из виджета."""
    meta = {name: html_entities.unescape(value) for name, value in META.findall(html)} if on_card else {}
    title = _title(html, meta) if on_card else None
    image = meta.get("og:image") if meta.get("og:image", "").startswith("https://") and len(meta["og:image"]) <= IMAGE_URL_MAX else None
    pairs = price_pairs(html)
    if not pairs:
        if OUT_OF_STOCK_WIDGET in html:
            widget_title, widget_image = _out_of_stock_widget(html)
            title, image = title or widget_title, image or widget_image
        if OUT_OF_STOCK_WIDGET in html or any(text in html.lower() for text in OUT_OF_STOCK_TEXTS):
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


def _out_of_stock_widget(html: str) -> tuple[str | None, str | None]:
    """Название и миниатюра закончившегося товара из виджета `webOutOfStock`."""
    start = html.find(OUT_OF_STOCK_WIDGET)
    end = html.find("data-widget=", start + len(OUT_OF_STOCK_WIDGET))
    chunk = html[start: end if end > 0 else None]
    texts = [" ".join(html_entities.unescape(piece).split()) for piece in TAG.split(chunk)]
    names = [text for text in texts if text and not AMOUNT.match(text) and text.lower() not in OUT_OF_STOCK_TEXTS]
    image = IMG_SRC.search(chunk)
    url = image.group(1) if image and len(image.group(1)) <= IMAGE_URL_MAX else None
    return (max(names, key=len)[:TITLE_MAX] if names else None), url


def _loaded(page: BrowserPage) -> bool:
    lowered = page.html.lower()
    sold_out = OUT_OF_STOCK_WIDGET in page.html or any(text in lowered for text in OUT_OF_STOCK_TEXTS)
    return PRICE_WIDGET in page.html or ('"location":{"current"' in page.html and sold_out)


def _title(html: str, meta: dict[str, str]) -> str | None:
    match = H1.search(html)
    raw = html_entities.unescape(TAG.sub("", match.group(1))) if match else meta.get("og:title", "")
    text = " ".join(raw.split())
    return text[:TITLE_MAX] or None
