"""Адаптер Ozon на сохранённой странице: цена и цена по карте из виджета, город из настроек страницы.

Образец tests/fixtures/stores/ozon/product_page.html — настоящая карточка от 06.10.2026, сокращённая:
og-метаданные, h1, виджет цены как есть, кнопка корзины и начало объекта с городом.
"""

from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.stores.ozon import OzonParser, canonical_url, price_pairs, selected_city
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

SAMPLE = (Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "ozon" / "product_page.html").read_text(encoding="utf-8")
URL = "https://www.ozon.ru/product/setevoe-zaryadnoe-ustroystvo-200vt-ugreen-nexode-x764-4-porta-fast-charger-s-tehnologiey-3220759448/"
WIDGET = 'data-widget="webPrice"'
THIN = "\u2009"  # Ozon разделяет разряды и «₽» тонким пробелом
CARD = f"6{THIN}466{THIN}₽"
REGULAR = f"7{THIN}185{THIN}₽"


class FakeBrowser:
    def __init__(self, html: str = SAMPLE, status: int = 200) -> None:
        self.html, self.status = html, status
        self.calls: list[tuple[str, str]] = []

    def fetch(self, url: NormalizedUrl, store_key: str, ready, timeout_s: float = 25.0, probe_js=None) -> BrowserPage:  # type: ignore[no-untyped-def]
        self.calls.append((url.url, store_key))
        page = BrowserPage(url, url.url, self.status, self.html)
        ready(page)
        return page


def parse(browser: FakeBrowser, url: str = URL, region: str = "spb") -> ParsedProduct:
    return OzonParser(browser).parse(normalize_url(url), region)  # type: ignore[arg-type]


def error(browser: FakeBrowser, **kwargs: str) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(browser, **kwargs)
    return caught.value


def test_regular_and_card_price_from_saved_page() -> None:
    assert parse(FakeBrowser()) == ParsedProduct(
        "Сетевое зарядное устройство 200Вт UGREEN Nexode X764, 4 порта Fast Charger с технологией GaN Infinity",
        718_500,
        646_600,
        Availability.IN_STOCK,
        "https://ir.ozone.ru/s3/multimedia-1-r/c600/11839110183.jpg",
        "spb",
        site_name="Ozon",
    )


def test_price_pairs_in_widget_order() -> None:
    assert price_pairs(SAMPLE) == [(646_600, "С банками"), (718_500, "С другими банками")]


def test_address_reduced_to_card() -> None:
    browser = FakeBrowser()

    parse(browser, url=URL + "?at=abc&sh=xyz&is_apparel_size_selected=true")

    assert browser.calls == [(URL, "ozon")]


def test_city_from_page_settings() -> None:
    assert selected_city(SAMPLE) == "Санкт-Петербург"


def test_single_price_is_regular_without_card() -> None:
    html = SAMPLE.replace("С другими банками", "").replace("С банками", "")
    product = parse(FakeBrowser(html))

    assert (product.price_minor, product.card_price_minor) == (646_600, None)


def test_card_price_not_lower_is_dropped() -> None:
    html = SAMPLE.replace(CARD, f"7{THIN}500{THIN}₽")

    assert parse(FakeBrowser(html)).card_price_minor is None


def test_no_cart_button_means_unknown_stock_with_price() -> None:
    html = SAMPLE.replace('data-widget="webAddToCart"', 'data-widget="webOther"')

    assert parse(FakeBrowser(html)).availability is Availability.UNKNOWN


def test_out_of_stock_page() -> None:
    html = SAMPLE.replace(WIDGET, 'data-widget="webOutOfStock"').replace("Добавить в корзину", "Этот товар закончился")

    product = parse(FakeBrowser(html))

    assert (product.availability, product.price_minor) == (Availability.OUT_OF_STOCK, None)


def test_other_city_is_region_mismatch() -> None:
    caught = error(FakeBrowser(SAMPLE.replace('"city":"Санкт-Петербург"', '"city":"Москва"')))

    assert (caught.code, caught.details["city"]) == (ErrorCode.REGION_MISMATCH, "Москва")


def test_unknown_region_code() -> None:
    assert error(FakeBrowser(), region="nsk").code is ErrorCode.REGION_MISMATCH


def test_protection_page_is_blocked() -> None:
    assert error(FakeBrowser("<html>Доступ ограничен. qrator</html>", status=403)).code is ErrorCode.BLOCKED


def test_page_without_widget_and_city_is_layout_change() -> None:
    assert error(FakeBrowser("<html><h1>Товар</h1></html>")).code is ErrorCode.LAYOUT_CHANGED


def test_page_without_city_settings_is_layout_change() -> None:
    html = SAMPLE.replace('"location":{"current"', '"place":{"current"')

    assert error(FakeBrowser(html)).code is ErrorCode.LAYOUT_CHANGED


def test_widget_without_amounts_is_layout_change() -> None:
    html = SAMPLE.replace(CARD, "скоро").replace(REGULAR, "скоро")

    assert error(FakeBrowser(html)).code is ErrorCode.LAYOUT_CHANGED


@pytest.mark.parametrize("raw", ["https://www.ozon.ru/", "https://www.ozon.ru/category/zaryadnye-ustroystva-15791/", "https://www.ozon.ru/product/abc/"])
def test_address_without_product_is_invalid(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        canonical_url(normalize_url(raw))

    assert caught.value.code is ErrorCode.INVALID_URL


# --- товар закончился: Ozon перенаправляет на поиск (настоящая страница от 06.10.2026) ---

OUT_OF_STOCK = (Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "ozon" / "out_of_stock_redirect.html").read_text(encoding="utf-8")
SEARCH_URL = "https://www.ozon.ru/search/?text=%D0%98%D0%BD%D1%81%D1%82%D1%80%D1%83%D0%BC%D0%B5%D0%BD%D1%82&product_id=3208684872"


class RedirectingBrowser(FakeBrowser):
    def fetch(self, url: NormalizedUrl, store_key: str, ready, timeout_s: float = 25.0, probe_js=None) -> BrowserPage:  # type: ignore[no-untyped-def]
        self.calls.append((url.url, store_key))
        page = BrowserPage(url, SEARCH_URL, self.status, self.html)
        ready(page)
        return page


def test_sold_out_product_redirected_to_search() -> None:
    product = parse(RedirectingBrowser(OUT_OF_STOCK), url="https://www.ozon.ru/product/3208684872/")

    assert (product.availability, product.price_minor) == (Availability.OUT_OF_STOCK, None)
    # Название и фото — из виджета закончившегося товара, а не страницы поиска (запрос и логотип Ozon).
    assert product.title == "Пластилин для чистки гаджетов, очиститель для телефона, наушников, динамиков и труднодоступных мест."
    assert product.image_url == "https://ir.ozone.ru/s3/multimedia-1-t/wc100/10563306365.jpg"


@pytest.mark.parametrize(
    "raw",
    [SEARCH_URL, "https://www.ozon.ru/category/zaryadnye-ustroystva-15791/?product_id=3208684872", "https://www.ozon.ru/search/?product_id=3208684872&text=x"],
)
def test_link_from_search_with_product_id_leads_to_card(raw: str) -> None:
    assert canonical_url(normalize_url(raw)).url == "https://www.ozon.ru/product/3208684872/"


@pytest.mark.parametrize(
    ("raw", "card"),
    [
        # Настоящая ссылка «Поделиться»: в названии есть «-1800r» и «-360-», артикул — только последнее число.
        (
            "https://www.ozon.ru/product/msi-34-monitor-izognutyy-mpg-341cqr-qd-oled-x36-krivizna-1800r-qd-oled-uwqhd-360-gts-0-03-4187696611/"
            "?from=share_ios&perehod=smm_share_button_productpage_link&short=1fMpFmB",
            "https://www.ozon.ru/product/msi-34-monitor-izognutyy-mpg-341cqr-qd-oled-x36-krivizna-1800r-qd-oled-uwqhd-360-gts-0-03-4187696611/",
        ),
        ("https://www.ozon.ru/product/kabel-2000-4187696611", "https://www.ozon.ru/product/kabel-2000-4187696611/"),
        ("https://www.ozon.ru/product/3208684872/?sh=x", "https://www.ozon.ru/product/3208684872/"),
    ],
)
def test_article_is_last_number_of_card_address(raw: str, card: str) -> None:
    assert canonical_url(normalize_url(raw)).url == card
