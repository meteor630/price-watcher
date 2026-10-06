"""pricewatch.parsers.universal: лестница «быстрый запрос → браузер», память способа, цена «глазами»."""

import json
from dataclasses import dataclass, field

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage
from pricewatch.parsers.contract import ParsedProduct, PriceSource
from pricewatch.parsers.fetch import FetchResponse
from pricewatch.parsers.universal import BROWSER, HTTP, InMemoryMethods, UniversalParser, profile_key
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

URL = normalize_url("https://www.shop.example/p/lamp")


def json_ld_page(price: str = "12990", site_name: str | None = "Магазин") -> str:
    product = {"@type": "Product", "name": "Лампа", "offers": {"price": price, "priceCurrency": "RUB", "availability": "InStock"}}
    meta = f'<meta property="og:site_name" content="{site_name}">' if site_name else ""
    return f'<html><head>{meta}<script type="application/ld+json">{json.dumps(product, ensure_ascii=False)}</script></head></html>'


def visual(amount: str | None = "12 990", *, buy: bool = True, oos: bool = False, h1: str = "Лампа «Ёжик»") -> dict:
    cands = [{"amount": amount, "size": 30, "weight": 700, "dy": 60, "dx": 0, "struck": False, "bad": False}] if amount else []
    return {"cands": cands, "h1": h1, "buy": buy, "oos": oos, "image": "https://img.shop.example/1.jpg"}


@dataclass
class FakeFetcher:
    status: int = 200
    html: str = ""
    content_type: str = "text/html; charset=utf-8"
    calls: int = 0

    def fetch(self, url: NormalizedUrl, *, accept: str = "text/html", cookies: dict | None = None) -> FetchResponse:
        self.calls += 1
        return FetchResponse(self.status, url, self.content_type, self.html.encode())


@dataclass
class FakeBrowser:
    html: str = "<html></html>"
    probe: object = None
    status: int = 200
    calls: list[str] = field(default_factory=list)

    def fetch(self, url, store_key, ready, timeout_s=25.0, probe_js=None):  # type: ignore[no-untyped-def]
        self.calls.append(store_key)
        assert probe_js is not None
        page = BrowserPage(url, url.url, self.status, self.html, probe=self.probe)
        ready(page)  # проверка готовности не должна падать ни на каких данных
        return page


def parser(fetcher: FakeFetcher, browser: FakeBrowser, methods: InMemoryMethods | None = None) -> UniversalParser:
    return UniversalParser(fetcher, browser, methods or InMemoryMethods())  # type: ignore[arg-type]


def error(fetcher: FakeFetcher, browser: FakeBrowser) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parser(fetcher, browser).parse(URL, "spb")
    return caught.value


# --- лестница способов ---


def test_fast_request_is_enough_and_remembered() -> None:
    methods = InMemoryMethods()
    browser = FakeBrowser()

    product = parser(FakeFetcher(html=json_ld_page()), browser, methods).parse(URL, "spb")

    assert (product.price_minor, product.source, product.site_name) == (1_299_000, PriceSource.SITE_DATA, "Магазин")
    assert browser.calls == []
    assert methods.get("shop.example") == HTTP


@pytest.mark.parametrize(
    "fetcher",
    [
        FakeFetcher(status=403),  # защита
        FakeFetcher(status=404),  # защита бывает и в виде 404 — окончательно верим только браузеру
        FakeFetcher(html="<html><body>загрузка…</body></html>"),  # страница без цены (сайт-приложение)
        FakeFetcher(content_type="application/json", html="{}"),
    ],
)
def test_fast_request_failure_goes_to_browser(fetcher: FakeFetcher) -> None:
    methods = InMemoryMethods()
    browser = FakeBrowser(html=json_ld_page("9990"))

    product = parser(fetcher, browser, methods).parse(URL, "spb")

    assert product.price_minor == 999_000
    assert browser.calls == ["site-shop.example"]
    assert methods.get("shop.example") == BROWSER


def test_remembered_browser_skips_fast_request() -> None:
    methods = InMemoryMethods()
    methods.remember("shop.example", BROWSER)
    fetcher = FakeFetcher(html=json_ld_page())

    parser(fetcher, FakeBrowser(html=json_ld_page()), methods).parse(URL, "spb")

    assert fetcher.calls == 0


def test_browser_failure_is_final_even_when_remembered() -> None:
    # Ответ браузера окончательный: при его ошибке быстрый запрос не пробуется.
    methods = InMemoryMethods()
    methods.remember("shop.example", BROWSER)
    fetcher = FakeFetcher(html=json_ld_page())

    with pytest.raises(PriceWatcherError):
        parser(fetcher, FakeBrowser(html="<html></html>", probe=visual(None, buy=False)), methods).parse(URL, "spb")
    assert fetcher.calls == 0


def test_foreign_currency_from_fast_request_is_final() -> None:
    page = json_ld_page().replace('"RUB"', '"USD"')
    browser = FakeBrowser()

    with pytest.raises(PriceWatcherError) as caught:
        parser(FakeFetcher(html=page), browser).parse(URL, "spb")

    assert caught.value.code is ErrorCode.UNSUPPORTED_CURRENCY
    assert browser.calls == []


# --- браузер: данные сайта и цена «глазами» ---


def test_price_by_eye_when_site_has_no_data() -> None:
    product = parser(FakeFetcher(status=403), FakeBrowser(probe=visual("4 590"))).parse(URL, "spb")

    assert product == ParsedProduct(
        "Лампа «Ёжик»", 459_000, None, Availability.IN_STOCK, "https://img.shop.example/1.jpg", "spb", PriceSource.PAGE
    )


def test_price_by_eye_without_buttons_notes_unknown_stock() -> None:
    product = parser(FakeFetcher(status=403), FakeBrowser(probe=visual("4 590", buy=False))).parse(URL, "spb")

    assert product.availability is Availability.UNKNOWN
    assert product.notes == ("наличие сайт явно не указал — считаю, что есть",)


def test_out_of_stock_by_buttons_keeps_last_price() -> None:
    product = parser(FakeFetcher(status=403), FakeBrowser(probe=visual("4 590", buy=False, oos=True))).parse(URL, "spb")

    assert (product.availability, product.price_minor) == (Availability.OUT_OF_STOCK, 459_000)


def test_out_of_stock_by_buttons_without_price() -> None:
    product = parser(FakeFetcher(status=403), FakeBrowser(probe=visual(None, buy=False, oos=True))).parse(URL, "spb")

    assert (product.availability, product.price_minor, product.source) == (Availability.OUT_OF_STOCK, None, PriceSource.PAGE)


def test_site_data_wins_and_big_difference_is_noted() -> None:
    product = parser(FakeFetcher(status=403), FakeBrowser(html=json_ld_page("10000"), probe=visual("11 000"))).parse(URL, "spb")

    assert (product.price_minor, product.source) == (1_000_000, PriceSource.SITE_DATA)
    assert product.notes == ("на странице крупно другая цена: 1100000 коп. (в данных сайта 1000000 коп.)",)


def test_difference_within_three_percent_is_not_noted() -> None:
    product = parser(FakeFetcher(status=403), FakeBrowser(html=json_ld_page("10000"), probe=visual("10 300"))).parse(URL, "spb")

    assert product.notes == ()


@pytest.mark.parametrize(
    ("browser", "code"),
    [
        (FakeBrowser(status=498, html="<html>antibot</html>", probe=visual(None, buy=False)), ErrorCode.BLOCKED),
        (FakeBrowser(status=404, html="<html>нет</html>", probe=visual(None, buy=False)), ErrorCode.PRICE_NOT_FOUND),
        (FakeBrowser(html="<html>пусто</html>", probe=visual(None, buy=False)), ErrorCode.PRICE_NOT_FOUND),
        (FakeBrowser(html="<html></html>", probe="мусор"), ErrorCode.PRICE_NOT_FOUND),
    ],
)
def test_browser_without_price(browser: FakeBrowser, code: ErrorCode) -> None:
    assert error(FakeFetcher(status=403), browser).code is code


def test_profile_key_is_safe_folder_name() -> None:
    assert profile_key("магазин.рф".encode("idna").decode()) == "site-xn--80aairftm.xn--p1ai"
    assert profile_key("a/../b") == "site-a_.._b"
