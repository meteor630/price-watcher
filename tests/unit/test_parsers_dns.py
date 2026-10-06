"""Адаптер DNS на сохранённых ответах: город из настроек страницы, цена из JSON microdata.

Образцы tests/fixtures/stores/dns от 06.10.2026: JSON /product/microdata/<id>/ как есть и страница
товара, сокращённая до заголовка и настроек шапки с выбранным городом.
"""

import copy
import json
from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, CapturedJson
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.stores.dns import DnsParser, canonical_url, selected_city
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "dns"
PAGE_HTML = (FIXTURES / "product_page.html").read_text(encoding="utf-8")
MICRODATA = json.loads((FIXTURES / "microdata.json").read_text(encoding="utf-8"))
URL = "https://www.dns-shop.ru/product/941bee591c43d582/korpus-lian-li-o11-vision-m-g99o11vmwr0-belyj/"
MICRO_URL = "https://www.dns-shop.ru/product/microdata/941bee59-1c43-11f1-9411-0050569d5825/"
SPB = "566ca284-5bea-11e2-aee1-00155d030b1f"


class FakeBrowser:
    def __init__(self, html: str = PAGE_HTML, microdata: object = MICRODATA, status: int = 200) -> None:
        body = microdata if isinstance(microdata, str) else json.dumps(microdata, ensure_ascii=False)
        captured = [CapturedJson(MICRO_URL, body, {})] if microdata is not None else []
        self.page_args = (html, status, captured)
        self.calls: list[tuple[str, str]] = []

    def fetch(self, url: NormalizedUrl, store_key: str, ready, timeout_s: float = 25.0, probe_js=None) -> BrowserPage:  # type: ignore[no-untyped-def]
        self.calls.append((url.url, store_key))
        html, status, captured = self.page_args
        page = BrowserPage(url, url.url, status, html, list(captured))
        assert ready(page) == bool(captured)
        return page


def parse(browser: FakeBrowser, url: str = URL, region: str = "spb") -> ParsedProduct:
    return DnsParser(browser).parse(normalize_url(url), region)  # type: ignore[arg-type]


def error(browser: FakeBrowser, **kwargs: str) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(browser, **kwargs)
    return caught.value


def micro_with(**offer: object) -> dict:
    data = copy.deepcopy(MICRODATA)
    data["data"]["offers"].update(offer)
    return data


def test_price_and_city_from_saved_sample() -> None:
    product = parse(FakeBrowser())

    assert (product.title, product.price_minor, product.availability, product.region_code, product.site_name) == (
        "Корпус LIAN LI O11 Vision-M [G99.O11VMW.R0] белый", 779_900, Availability.IN_STOCK, "spb", "DNS"
    )
    assert product.image_url is not None and product.image_url.startswith("https://")


def test_address_is_reduced_to_product_card() -> None:
    browser = FakeBrowser()

    parse(browser, url=URL + "?utm_source=x&from=search#reviews")

    assert browser.calls == [(URL, "dns")]


def test_city_from_page_settings() -> None:
    assert selected_city(PAGE_HTML) == SPB


def test_out_of_stock() -> None:
    product = parse(FakeBrowser(microdata=micro_with(availability="https://schema.org/OutOfStock")))

    assert product.availability is Availability.OUT_OF_STOCK


def test_fractional_price_without_float() -> None:
    assert parse(FakeBrowser(microdata=micro_with(price=7799.5))).price_minor == 779_950


def test_other_city_is_region_mismatch() -> None:
    caught = error(FakeBrowser(html=PAGE_HTML.replace(SPB, "30b7c1f3-03fb-11dc-95ee-00151716f9f5")))

    assert caught.code is ErrorCode.REGION_MISMATCH


def test_unknown_region_code() -> None:
    assert error(FakeBrowser(), region="nsk").code is ErrorCode.REGION_MISMATCH


def test_qrator_page_is_blocked() -> None:
    assert error(FakeBrowser(html="<html>qrator</html>", microdata=None, status=401)).code is ErrorCode.BLOCKED


def test_page_without_microdata_is_layout_change() -> None:
    assert error(FakeBrowser(microdata=None)).code is ErrorCode.LAYOUT_CHANGED


@pytest.mark.parametrize(
    "microdata",
    ["{broken", {"result": False, "data": None}, {"result": True, "data": {"@type": "Organization"}}, {"result": True}],
)
def test_changed_microdata_is_layout_change(microdata: object) -> None:
    assert error(FakeBrowser(microdata=microdata)).code is ErrorCode.LAYOUT_CHANGED


def test_page_without_city_settings_is_layout_change() -> None:
    assert error(FakeBrowser(html="<html><h1>Корпус</h1></html>")).code is ErrorCode.LAYOUT_CHANGED


def test_foreign_currency() -> None:
    assert error(FakeBrowser(microdata=micro_with(priceCurrency="KZT"))).code is ErrorCode.UNSUPPORTED_CURRENCY


@pytest.mark.parametrize("raw", ["https://www.dns-shop.ru/", "https://www.dns-shop.ru/catalog/17a89aab16404e77/videokarty/", "https://www.dns-shop.ru/product/xyz/"])
def test_address_without_product_is_invalid(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        canonical_url(normalize_url(raw))

    assert caught.value.code is ErrorCode.INVALID_URL
