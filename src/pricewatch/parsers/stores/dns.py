"""DNS (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Сайт закрыт защитой Qrator: только браузер (patchright, профиль магазина). Город DNS выбирает
по IP и хранит в подписанной cookie, подделать её нельзя; выбранный город страница кладёт в свои
настройки как `cityGuid` — по нему город и подтверждается. Цена, наличие, название и фото —
из JSON `/product/microdata/<id>/`, который страница запрашивает сама: это запись schema.org Product
(цена в рублях целым числом), её разбирает та же логика предложений, что и JSON-LD.
"""

import json
import re

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.browser import BrowserPage, CapturedJson, StoreBrowser, looks_blocked
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.generic import product_from_json_ld
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

KEY = "dns"
PRODUCT_PATH = re.compile(r"^/product/[0-9a-f]{12,32}/[^?#]*")
MICRODATA_PATH = "/product/microdata/"
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
        page = self._browser.fetch(page_url, KEY, lambda page: _microdata(page) is not None)
        captured = _microdata(page)
        if captured is None:
            raise PriceWatcherError(ErrorCode.BLOCKED if looks_blocked(page) else ErrorCode.LAYOUT_CHANGED)
        page_city = selected_city(page.html)
        if page_city != city:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"city": page_city})
        return product_from_microdata(captured.body, page_url, region_code)


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


def _microdata(page: BrowserPage) -> CapturedJson | None:
    return next((captured for captured in page.json if MICRODATA_PATH in captured.url), None)
