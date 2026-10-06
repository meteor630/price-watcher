"""Общий парсер для магазина без своего адаптера (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Источники по очереди: JSON-LD (schema.org Product/Offer, включая `@graph`
и AggregateOffer) → Open Graph и meta (`product:price:amount`) → микроразметка
(`itemprop="price"`). Видимый текст цену не задаёт: из него только предлагаются
кандидаты для администратора, который подтверждает новый магазин. Цен по карте
общий парсер не знает. Регион подтверждается тем, что администратор подтвердил
магазин; общий парсер возвращает запрошенный.

Сломанный JSON-LD в одном блоке не мешает остальным источникам — так и задуман
порядок «по очереди», а не подмена ошибки.
"""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.contract import IMAGE_URL_MAX, TITLE_MAX, ParsedProduct
from pricewatch.parsers.fetch import Fetcher, FetchResponse
from pricewatch.parsers.prices import is_ruble, structured_price, text_candidates
from pricewatch.parsers.urls import NormalizedUrl

HTML_TYPES = ("text/html", "application/xhtml+xml")
IN_STOCK = ("instock", "in stock", "limitedavailability", "onlineonly", "instoreonly", "preorder", "presale", "backorder")
OUT_OF_STOCK = ("outofstock", "out of stock", "oos", "soldout", "discontinued")
SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
META_PRICE = ("product:price:amount", "og:price:amount")
META_CURRENCY = ("product:price:currency", "og:price:currency")
META_AVAILABILITY = ("product:availability", "og:availability")


@dataclass
class _Page:
    json_ld: list[str] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    itemprops: dict[str, str] = field(default_factory=dict)
    title: str = ""
    texts: list[str] = field(default_factory=list)


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = _Page()
        self._skip_depth = 0
        self._in_json_ld = False
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "script" and values.get("type", "").strip().lower() == "application/ld+json":
            self._in_json_ld = True
            self.page.json_ld.append("")
        if tag in SKIPPED_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").strip().lower()
            if key and "content" in values:
                self.page.meta.setdefault(key, values["content"])
        prop = values.get("itemprop", "").strip()
        if prop:
            value = values.get("content") or values.get("href")
            if value:
                self.page.itemprops.setdefault(prop, value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_json_ld = False
        if tag in SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self.page.json_ld[-1] += data
        elif self._in_title:
            self.page.title += data
        elif not self._skip_depth and data.strip():
            self.page.texts.append(" ".join(data.split()))


@dataclass(frozen=True)
class _Offer:
    price_minor: int | None
    currency: str | None
    availability: Availability


class GenericParser:
    key = "generic"

    def __init__(self, fetcher: Fetcher) -> None:
        self._fetcher = fetcher

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        response = self._fetcher.fetch(url)
        check_status(response)
        if response.content_type.split(";")[0].strip().lower() not in HTML_TYPES:
            raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "not_html"})
        return parse_html(response.text(), response.url, region_code)


def check_status(response: FetchResponse) -> None:
    """Код ответа магазина → ошибка сбора цен; 200 проходит."""
    status = response.status
    if status == 200:
        return
    if status in (404, 410):
        raise PriceWatcherError(ErrorCode.PRICE_NOT_FOUND, {"status": status})
    if status in (401, 403, 429):
        raise PriceWatcherError(ErrorCode.BLOCKED, {"status": status})
    if status >= 500:
        raise PriceWatcherError(ErrorCode.TIMEOUT, {"status": status})
    raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"status": status})


def parse_html(html: str, page_url: NormalizedUrl, region_code: str) -> ParsedProduct:
    collector = _Collector()
    collector.feed(html)
    collector.close()
    page = collector.page

    products = [product for block in page.json_ld for product in _json_ld_products(block)]
    # Источники по очереди, без смешивания: микроразметка ниже на странице бывает у рекомендованных
    # товаров, и её цена не должна перебивать цену самого товара из JSON-LD.
    sources = ([offer for product in products for offer in _product_offers(product)], _meta_offers(page), _microdata_offers(page))
    offers = next((source for source in sources if _informative(source)), [])
    title = _first_text([_text(product.get("name")) for product in products] + [page.meta.get("og:title"), page.itemprops.get("name"), page.title])
    image = _image([_image_url(product.get("image")) for product in products] + [page.meta.get("og:image")], page_url)

    priced = [offer for offer in offers if offer.price_minor is not None]
    rubles = [offer for offer in priced if is_ruble(offer.currency)]
    if priced and not rubles:
        raise PriceWatcherError(ErrorCode.UNSUPPORTED_CURRENCY)
    in_stock = [offer for offer in rubles if offer.availability is not Availability.OUT_OF_STOCK]
    if in_stock:
        best = min(in_stock, key=lambda offer: offer.price_minor or 0)
        availability = Availability.IN_STOCK if best.availability is Availability.IN_STOCK else Availability.UNKNOWN
        return ParsedProduct(title, best.price_minor, None, availability, image, region_code)
    if any(offer.availability is Availability.OUT_OF_STOCK for offer in offers):
        return ParsedProduct(title, None, None, Availability.OUT_OF_STOCK, image, region_code)
    raise PriceWatcherError(ErrorCode.PRICE_NOT_FOUND, {"candidates": list(text_candidates(page.texts))})


def _json_ld_products(block: str) -> Iterator[Mapping[str, Any]]:
    try:
        data = json.loads(block, parse_float=Decimal)
    except ValueError:
        return  # один сломанный блок — остальные источники ещё впереди
    yield from _walk_products(data)


def _walk_products(node: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(node, list):
        for value in node:
            yield from _walk_products(value)
    elif isinstance(node, dict):
        types = node.get("@type")
        type_names = {types} if isinstance(types, str) else set(types) if isinstance(types, list) else set()
        if type_names & {"Product", "ProductModel", "IndividualProduct"}:
            # Внутрь товара не заходим: там бывают похожие и сопутствующие товары со своими ценами.
            yield node
        elif "ProductGroup" in type_names:
            yield node
            yield from _walk_products(node.get("hasVariant"))
        else:
            for value in node.values():
                yield from _walk_products(value)


def _product_offers(product: Mapping[str, Any]) -> Iterator[_Offer]:
    offers = product.get("offers")
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, dict):
            continue
        nested = offer.get("offers")
        if isinstance(nested, (list, dict)):
            yield from _product_offers({"offers": nested})
        specification = offer.get("priceSpecification")
        specification = specification if isinstance(specification, dict) else {}
        raw_price = next((value for value in (offer.get("price"), offer.get("lowPrice"), specification.get("price")) if value is not None), None)
        currency = offer.get("priceCurrency") or specification.get("priceCurrency")
        yield _Offer(_price(raw_price), _text(currency), _availability(_text(offer.get("availability"))))


def _informative(offers: list[_Offer]) -> bool:
    return any(offer.price_minor is not None or offer.availability is not Availability.UNKNOWN for offer in offers)


def _meta_offers(page: _Page) -> list[_Offer]:
    meta_price = next((page.meta[key] for key in META_PRICE if key in page.meta), None)
    meta_availability = next((page.meta[key] for key in META_AVAILABILITY if key in page.meta), None)
    if meta_price is None and meta_availability is None:
        return []
    currency = next((page.meta[key] for key in META_CURRENCY if key in page.meta), None)
    return [_Offer(_price(meta_price), currency, _availability(meta_availability))]


def _microdata_offers(page: _Page) -> list[_Offer]:
    if "price" not in page.itemprops and "availability" not in page.itemprops:
        return []
    return [_Offer(_price(page.itemprops.get("price")), page.itemprops.get("priceCurrency"), _availability(page.itemprops.get("availability")))]


def _price(value: object) -> int | None:
    if value is None:
        return None
    try:
        return structured_price(value)
    except ValueError:
        return None  # «по запросу», «0», три знака копеек — цены нет


def _availability(value: str | None) -> Availability:
    if not value:
        return Availability.UNKNOWN
    word = value.strip().lower().rsplit("/", 1)[-1]
    if word in OUT_OF_STOCK:
        return Availability.OUT_OF_STOCK
    if word in IN_STOCK:
        return Availability.IN_STOCK
    return Availability.UNKNOWN


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _first_text(values: list[str | None]) -> str | None:
    for value in values:
        if value and (text := " ".join(value.split())):
            return text[:TITLE_MAX]
    return None


def _image_url(value: object) -> str | None:
    if isinstance(value, list):
        return next((url for item in value if (url := _image_url(item))), None)
    if isinstance(value, dict):
        return _text(value.get("url")) or _text(value.get("contentUrl"))
    return _text(value)


def _image(values: list[str | None], page_url: NormalizedUrl) -> str | None:
    for value in values:
        if value:
            url = urljoin(page_url.url, value.strip())
            if url.startswith("https://") and len(url) <= IMAGE_URL_MAX:
                return url
    return None
