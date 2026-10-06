"""Wildberries (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Без браузера Wildberries не отвечает: и страница, и JSON закрыты защитой wbaas (06.10.2026).
Поэтому страница товара открывается в браузере с профилем магазина, а дальше изнутри неё:

1. дожидаемся собственного запроса карточки от сайта: из него берутся заголовки, без которых
   Wildberries отвечает 403 (`deviceid` и два служебных; cookies не сохраняются);
2. сервис геоданных Wildberries называет город и его `dest` — так подтверждается регион
   (сама страница без выбранного адреса берёт Москву, поэтому её собственный ответ не годится:
   цены в Санкт-Петербурге другие, 06.10.2026 — на 4 % ниже);
3. JSON карточки запрашивается с этим `dest` — цены и наличие именно для нашего города.

Цены в JSON — в копейках: `price.product` — цена покупателя, `price.basic` — зачёркнутая.
Цену с WB Кошельком сервер не присылает (её считает страница), поэтому цены по карте нет.
Фото — по адресу карточки на CDN, который страница запрашивает сама.
"""

import json
import re
from typing import Any
from urllib.parse import quote

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, CapturedJson, JsonReply, StoreBrowser, looks_blocked
from pricewatch.parsers.contract import TITLE_MAX, ParsedProduct
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

KEY = "wildberries"
ARTICLE = re.compile(r"^/catalog/(\d{1,15})/")
# Город региона и точка в нём: по ней сервис геоданных выбирает склады доставки.
REGIONS = {"spb": ("Санкт-Петербург", 59.9386, 30.3141)}
CARD_CDN = re.compile(r"^(https://[a-z0-9.-]+\.wbbasket\.ru/vol\d+/part\d+/(\d+))/info/")
DETAIL_PATH = "/__internal/u-card/cards/v4/detail"
SITE_HEADERS = ("deviceid", "x-requested-with", "x-spa-version")


class WildberriesParser:
    key = KEY
    hosts = frozenset({"wildberries.ru"})

    def __init__(self, browser: StoreBrowser) -> None:
        self._browser = browser

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        article = article_id(url)
        if region_code not in REGIONS:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"region": region_code})
        city, latitude, longitude = REGIONS[region_code]
        page_url = normalize_url(f"https://www.wildberries.ru/catalog/{article}/detail.aspx")
        geo_url = normalize_url(
            "https://user-geo-data.wildberries.ru/get-geo-info"
            f"?currency=RUB&latitude={latitude}&longitude={longitude}&locale=ru&address={quote(city)}&dt=0"
        )
        with self._browser.open(page_url, KEY, _site_asked_card) as opened:
            site_request = _site_card_request(opened.page)
            if site_request is None:
                raise PriceWatcherError(ErrorCode.BLOCKED if looks_blocked(opened.page) else ErrorCode.LAYOUT_CHANGED)
            headers = site_headers(site_request)
            (geo,) = opened.fetch_json([geo_url])
            dest = region_dest(geo, city)
            detail_url = normalize_url(f"https://www.wildberries.ru{DETAIL_PATH}?appType=1&curr=rub&dest={dest}&spp=30&nm={article}")
            (detail,) = opened.fetch_json([detail_url], headers)
            return product_from_detail(detail, article, region_code, card_image(opened.page, article))


def article_id(url: NormalizedUrl) -> int:
    """Артикул из адреса вида /catalog/<артикул>/detail.aspx."""
    match = ARTICLE.match(url.url.split(url.host, 1)[1])
    if match is None:
        raise PriceWatcherError(ErrorCode.INVALID_URL)
    return int(match.group(1))


def region_dest(reply: JsonReply, city: str) -> int:
    """`dest` нашего города по ответу сервиса геоданных; другой город — REGION_MISMATCH."""
    data = _json(reply)
    if not isinstance(data, dict) or not isinstance(data.get("xinfo"), str):
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "geo_format"})
    if data.get("address") != city:
        raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"city": str(data.get("address"))[:100]})
    match = re.search(r"(?:^|&)dest=(-?\d{1,12})(?:&|$)", data["xinfo"])
    if match is None:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "geo_dest"})
    return int(match.group(1))


def product_from_detail(reply: JsonReply, article: int, region_code: str, image_url: str | None) -> ParsedProduct:
    data = _json(reply)
    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list):
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "detail_format"})
    product = next((item for item in products if isinstance(item, dict) and item.get("id") == article), None)
    if product is None:
        raise PriceWatcherError(ErrorCode.PRICE_NOT_FOUND, {"reason": "no_such_article"})

    prices = [_size_price(size) for size in product.get("sizes") or [] if isinstance(size, dict)]
    prices = [price for price in prices if price is not None]
    quantity = product.get("totalQuantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "quantity"})
    name = product.get("name")
    title = " ".join(name.split())[:TITLE_MAX] if isinstance(name, str) and name.strip() else None
    if quantity == 0:
        return ParsedProduct(title, None, None, Availability.OUT_OF_STOCK, image_url, region_code)
    if not prices:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_price_in_stock"})
    return ParsedProduct(title, min(prices), None, Availability.IN_STOCK, image_url, region_code)


def site_headers(request: CapturedJson) -> dict[str, str]:
    """Служебные заголовки сайта для своего запроса карточки; без `deviceid` Wildberries отвечает 403."""
    headers = {name: request.request_headers[name] for name in SITE_HEADERS if name in request.request_headers}
    if "deviceid" not in headers:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_deviceid"})
    return headers


def card_image(page: BrowserPage, article: int) -> str | None:
    """Первое фото товара: рядом с карточкой на CDN, адрес которой страница запросила сама."""
    for captured in page.json:
        match = CARD_CDN.match(captured.url)
        if match and match.group(2) == str(article):
            return f"{match.group(1)}/images/big/1.webp"
    return None


def _site_card_request(page: BrowserPage) -> CapturedJson | None:
    return next((captured for captured in page.json if DETAIL_PATH in captured.url), None)


def _site_asked_card(page: BrowserPage) -> bool:
    # Сайт сам запросил карточку — значит, проверка защиты пройдена и cookies выданы.
    return _site_card_request(page) is not None


def _size_price(size: dict[str, Any]) -> int | None:
    price = size.get("price")
    value = price.get("product") if isinstance(price, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _json(reply: JsonReply) -> Any:
    if reply.status in (401, 403, 429, 498):
        raise PriceWatcherError(ErrorCode.BLOCKED, {"status": reply.status})
    if reply.status != 200 or reply.body is None:
        code = ErrorCode.TIMEOUT if reply.status == 0 or reply.status >= 500 else ErrorCode.LAYOUT_CHANGED
        raise PriceWatcherError(code, {"status": reply.status})
    try:
        return json.loads(reply.body)
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "not_json"}) from error
