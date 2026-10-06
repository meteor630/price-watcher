"""Регард (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Без браузера: страница отдаётся обычным запросом (06.10.2026). Цена, наличие, название и фото —
из JSON-LD общим парсером. Город выбирается cookie `city_id` (Санкт-Петербург — 137) и
подтверждается состоянием самой страницы: Next.js кладёт выбранный город в `__NEXT_DATA__`
как `town.cookieTownId`. Другой город или нет такого поля — цену не берём.
"""

import json
import re
from typing import Any

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.fetch import Fetcher
from pricewatch.parsers.generic import check_status, parse_html
from pricewatch.parsers.urls import NormalizedUrl

KEY = "regard"
REGION_TOWNS = {"spb": 137}
PRODUCT_PATH = re.compile(r"^/product/\d+(/|$)")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


class RegardParser:
    key = KEY
    hosts = frozenset({"regard.ru"})

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        if not PRODUCT_PATH.match(url.url.split(url.host, 1)[1]):
            raise PriceWatcherError(ErrorCode.INVALID_URL)
        town = REGION_TOWNS.get(region_code)
        if town is None:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"region": region_code})
        response = self._fetcher.fetch(url, cookies={"city_id": str(town)})
        check_status(response)
        html = response.text()
        page_town = selected_town(html)
        if page_town != town:
            raise PriceWatcherError(ErrorCode.REGION_MISMATCH, {"town": page_town})
        return parse_html(html, response.url, region_code)


def selected_town(html: str) -> int | None:
    """Город, для которого страница посчитана: `town.cookieTownId` из `__NEXT_DATA__`."""
    match = NEXT_DATA.search(html)
    if match is None:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_next_data"})
    try:
        data = json.loads(match.group(1))
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "next_data_json"}) from error
    town = _find_town(data)
    if town is None:
        raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "no_town"})
    value = town.get("cookieTownId")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _find_town(node: Any) -> dict[str, Any] | None:
    # Путь к состоянию города в Next.js меняется от версии к версии — ищем сам объект `town`.
    if isinstance(node, dict):
        town = node.get("town")
        if isinstance(town, dict) and "cookieTownId" in town:
            return town
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_town(child)
        if found is not None:
            return found
    return None
