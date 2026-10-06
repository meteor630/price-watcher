"""Адаптер Авито на сохранённом объявлении: цена продавца из JSON-LD, снятое объявление, защита.

Образец tests/fixtures/stores/avito/listing.html — настоящее объявление от 06.10.2026, сокращённое
до og-метаданных, JSON-LD и h1; текст объявления частного продавца заменён.
"""

from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.stores.avito import AvitoParser, canonical_url
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

SAMPLE = (Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "avito" / "listing.html").read_text(encoding="utf-8")
URL = "https://www.avito.ru/sankt-peterburg/mebel_i_interer/nastolnaya_lampa_na_strubtsine_8457677995"


class FakeBrowser:
    def __init__(self, html: str = SAMPLE, status: int = 200) -> None:
        self.html, self.status = html, status
        self.calls: list[tuple[str, str]] = []

    def fetch(self, url: NormalizedUrl, store_key: str, ready, timeout_s: float = 25.0, probe_js=None) -> BrowserPage:  # type: ignore[no-untyped-def]
        self.calls.append((url.url, store_key))
        page = BrowserPage(url, url.url, self.status, self.html)
        ready(page)
        return page


def parse(browser: FakeBrowser, url: str = URL) -> ParsedProduct:
    return AvitoParser(browser).parse(normalize_url(url), "spb")  # type: ignore[arg-type]


def error(browser: FakeBrowser) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(browser)
    return caught.value


def test_listing_from_saved_page() -> None:
    assert parse(FakeBrowser()) == ParsedProduct(
        "Настольная лампа на струбцине", 50_000, None, Availability.IN_STOCK, "https://www.avito.ru/img/share/auto/58231333972", "spb", site_name="Авито"
    )


def test_address_reduced_to_listing() -> None:
    browser = FakeBrowser()

    parse(browser, url=URL + "?context=H4sIAAA&slocation=653240#block")

    assert browser.calls == [(URL, "avito")]


def test_closed_listing() -> None:
    html = "<html><body><h1>Настольная лампа на струбцине</h1><p>Объявление снято с публикации</p></body></html>"

    product = parse(FakeBrowser(html))

    assert (product.title, product.price_minor, product.availability) == ("Настольная лампа на струбцине", None, Availability.OUT_OF_STOCK)


def test_sold_listing_marked_in_json_ld() -> None:
    html = SAMPLE.replace("http://schema.org/InStock", "http://schema.org/OutOfStock")

    assert parse(FakeBrowser(html)).availability is Availability.OUT_OF_STOCK


@pytest.mark.parametrize(
    ("html", "status", "code"),
    [
        ("<html>Доступ ограничен: проблема с IP. captcha</html>", 429, ErrorCode.CAPTCHA),
        ("<html>firewall captcha</html>", 403, ErrorCode.CAPTCHA),
        ("<html>Такой страницы нет</html>", 404, ErrorCode.PRICE_NOT_FOUND),
        ("<html><h1>Объявление</h1></html>", 200, ErrorCode.LAYOUT_CHANGED),
    ],
)
def test_no_listing_data(html: str, status: int, code: ErrorCode) -> None:
    assert error(FakeBrowser(html, status)).code is code


@pytest.mark.parametrize("raw", ["https://www.avito.ru/", "https://www.avito.ru/sankt-peterburg?q=лампа", "https://www.avito.ru/sankt-peterburg/mebel_i_interer"])
def test_address_without_listing_is_invalid(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        canonical_url(normalize_url(raw))

    assert caught.value.code is ErrorCode.INVALID_URL
