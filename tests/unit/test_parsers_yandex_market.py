"""Адаптер Яндекс Маркета на сохранённых страницах: цены с картой Пэй и без, регион, «нет в продаже».

Образцы tests/fixtures/stores/yandex_market от 06.10.2026 — настоящие карточки, сокращённые:
JSON-LD товара, h1, начало объекта региона и блок priceDetails как есть; у снятого с продажи
товара — isNotOnSale.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.fetch import FetchResponse
from pricewatch.parsers.stores.yandex_market import YandexMarketParser, canonical_url, price_details, selected_region
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "yandex_market"
IN_STOCK = (FIXTURES / "in_stock.html").read_text(encoding="utf-8")
NOT_ON_SALE = (FIXTURES / "not_on_sale.html").read_text(encoding="utf-8")
CARD = "https://market.yandex.ru/card/dpn-router-deeper-connect-mini-i-apparatnyy-klyuch-yubikey-security-key-nfc-usb-a/5227893665"
SOLD_OUT = "https://market.yandex.ru/card/hagibis-box-opener--ejection-pin-box-opener-version/5765014011"


@dataclass
class FakeFetcher:
    html: str = IN_STOCK
    status: int = 200
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: NormalizedUrl, *, accept: str = "text/html", cookies: dict | None = None) -> FetchResponse:
        self.calls.append(url.url)
        return FetchResponse(self.status, url, "text/html; charset=utf-8", self.html.encode())


@dataclass
class FakeBrowser:
    html: str = ""
    status: int = 200
    final_url: str | None = None
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: NormalizedUrl, store_key: str, ready, timeout_s: float = 25.0, probe_js=None) -> BrowserPage:  # type: ignore[no-untyped-def]
        self.calls.append(store_key)
        page = BrowserPage(url, self.final_url or url.url, self.status, self.html)
        ready(page)
        return page


def parse(fetcher: FakeFetcher, browser: FakeBrowser | None = None, url: str = CARD, region: str = "spb") -> ParsedProduct:
    return YandexMarketParser(fetcher, browser or FakeBrowser()).parse(normalize_url(url), region)  # type: ignore[arg-type]


def error(fetcher: FakeFetcher, browser: FakeBrowser | None = None, **kwargs: str) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(fetcher, browser, **kwargs)
    return caught.value


def test_regular_price_without_card_and_yandex_pay_price() -> None:
    product = parse(FakeFetcher())

    # 50 117 ₽ в JSON-LD — цена с картой Пэй; обычная — «без карты».
    assert (product.price_minor, product.card_price_minor) == (5_114_000, 5_011_700)
    assert (product.title, product.availability, product.site_name) == (
        "DPN Роутер Deeper Connect Mini и аппаратный ключ Yubikey Security Key NFC USB-A", Availability.IN_STOCK, "Яндекс Маркет"
    )
    assert product.image_url is not None and product.image_url.startswith("https://avatars.mds.yandex.net/")


def test_price_details_pairs() -> None:
    assert price_details(IN_STOCK) == [(5_011_700, "Пэй • Открыть"), (5_114_000, "без карты")]


def test_without_price_details_json_ld_price_is_used_with_note() -> None:
    html = IN_STOCK.replace('"priceDetails"', '"otherDetails"')

    product = parse(FakeFetcher(html))

    assert (product.price_minor, product.card_price_minor) == (5_011_700, None)
    assert product.notes == ("цена может быть с картой Яндекс Пэй: блока с ценой без карты на странице нет",)


def test_not_on_sale() -> None:
    product = parse(FakeFetcher(NOT_ON_SALE), url=SOLD_OUT)

    assert product == ParsedProduct(
        "Hagibis Box Opener + Ejection Pin, Box Opener Version", None, None, Availability.OUT_OF_STOCK, None, "spb", site_name="Яндекс Маркет"
    )


def test_advert_parameters_are_dropped() -> None:
    fetcher = FakeFetcher()

    parse(fetcher, url=CARD + "?do-waremd5=EHTU&sponsored=1&cpc=JU4d&nid=70161135&ogV=352")

    assert fetcher.calls == [CARD]


def test_region_from_page_data() -> None:
    assert selected_region(IN_STOCK) == (2, "Санкт-Петербург")


def test_other_region_is_mismatch() -> None:
    html = IN_STOCK.replace('"region":{"id":2', '"region":{"id":213').replace('"name":"Санкт-Петербург"', '"name":"Москва"')

    caught = error(FakeFetcher(html))

    assert (caught.code, caught.details["city"]) == (ErrorCode.REGION_MISMATCH, "Москва")


def test_unknown_region_code() -> None:
    assert error(FakeFetcher(), region="nsk").code is ErrorCode.REGION_MISMATCH


def test_captcha_for_fast_request_goes_to_browser() -> None:
    browser = FakeBrowser(IN_STOCK)

    product = parse(FakeFetcher("<html>captcha</html>", status=403), browser)

    assert browser.calls == ["yandex_market"]
    assert product.price_minor == 5_114_000


def test_captcha_in_browser_too() -> None:
    browser = FakeBrowser("<html>Подтвердите, что вы не робот</html>", final_url="https://market.yandex.ru/showcaptcha?retpath=x")

    assert error(FakeFetcher("", status=403), browser).code is ErrorCode.CAPTCHA


def test_missing_card_in_browser() -> None:
    assert error(FakeFetcher("", status=404), FakeBrowser("<html>нет</html>", status=404)).code is ErrorCode.PRICE_NOT_FOUND


def test_page_without_product_data_is_layout_change() -> None:
    html = IN_STOCK.split("<body>")[0] + "<body><h1>Товар</h1></body></html>"

    assert error(FakeFetcher(html), FakeBrowser(html)).code is ErrorCode.LAYOUT_CHANGED


@pytest.mark.parametrize(
    ("raw", "card"),
    [
        (CARD + "?cpc=x", CARD),
        ("https://market.yandex.ru/product--dpn-router/5227893665?sku=1", "https://market.yandex.ru/product--dpn-router/5227893665"),
    ],
)
def test_canonical_card(raw: str, card: str) -> None:
    assert canonical_url(normalize_url(raw)).url == card


@pytest.mark.parametrize("raw", ["https://market.yandex.ru/", "https://market.yandex.ru/catalog--routery/1/list", "https://market.yandex.ru/card/x/abc"])
def test_address_without_card_is_invalid(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        canonical_url(normalize_url(raw))

    assert caught.value.code is ErrorCode.INVALID_URL


def test_broken_price_value_in_details_is_layout_change() -> None:
    html = IN_STOCK.replace('"value": "51140.000000"', '"value": "51140.123000"').replace('"value":"51140.000000"', '"value":"51140.123000"')

    assert error(FakeFetcher(html)).code is ErrorCode.LAYOUT_CHANGED
