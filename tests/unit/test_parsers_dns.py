"""Адаптер DNS на сохранённых ответах: город из настроек страницы, название и фото из JSON microdata, цена и наличие —
из блока покупки, который страница догружает сама.

Образцы tests/fixtures/stores/dns от 13.10.2026, ответы как есть (Санкт-Петербург, гость):
- in_stock — корпус Lian Li: в магазине, пункты выдачи, доставка;
- delivery_only — материнская плата GIGABYTE: в магазинах нет (в microdata OutOfStock), но пункты выдачи и доставка
  есть, цена на кнопке «Купить» (31 799 ₽) ниже цены в microdata (33 799 ₽);
- unavailable — память ADATA: «Нет в наличии», notAvail, «последняя цена продажи».
Страница товара product_page.html сокращена до настроек шапки с выбранным городом.
"""

import copy
import json
from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, CapturedJson
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.stores.dns import AVAILS_PATH, BUY_PATH, MICRODATA_PATH, DnsParser, canonical_url, selected_city
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "dns"
PAGE_HTML = (FIXTURES / "product_page.html").read_text(encoding="utf-8")
URL = "https://www.dns-shop.ru/product/941bee591c43d582/korpus-lian-li-o11-vision-m-g99o11vmwr0-belyj/"
SPB = "566ca284-5bea-11e2-aee1-00155d030b1f"
ORIGIN = "https://www.dns-shop.ru"


def sample(case: str, part: str) -> dict:
    return json.loads((FIXTURES / f"{case}_{part}.json").read_text(encoding="utf-8"))


def responses(case: str = "in_stock", **override: object) -> dict[str, object]:
    """Три ответа страницы товара; None — ответ не пришёл; строка — тело как есть."""
    found: dict[str, object] = {
        MICRODATA_PATH: sample(case, "microdata"), BUY_PATH: sample(case, "product_buy"), AVAILS_PATH: sample(case, "avails"),
    }
    found.update({{"microdata": MICRODATA_PATH, "buy": BUY_PATH, "avails": AVAILS_PATH}[key]: value for key, value in override.items()})
    return found


class FakeBrowser:
    def __init__(self, html: str = PAGE_HTML, found: dict[str, object] | None = None, status: int = 200) -> None:
        captured = []
        for path, body in (responses() if found is None else found).items():
            if body is not None:
                text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                captured.append(CapturedJson(ORIGIN + path + ("x/" if path == MICRODATA_PATH else ""), text, {}))
        self.page_args = (html, status, captured)
        self.ready_seen: list[bool] = []
        self.calls: list[tuple[str, str]] = []

    def fetch(self, url: NormalizedUrl, store_key: str, ready, timeout_s: float = 25.0, probe_js=None) -> BrowserPage:  # type: ignore[no-untyped-def]
        self.calls.append((url.url, store_key))
        html, status, captured = self.page_args
        page = BrowserPage(url, url.url, status, html, list(captured))
        self.ready_seen.append(ready(page))
        return page


def parse(browser: FakeBrowser, url: str = URL, region: str = "spb") -> ParsedProduct:
    return DnsParser(browser).parse(normalize_url(url), region)  # type: ignore[arg-type]


def error(browser: FakeBrowser, **kwargs: str) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(browser, **kwargs)
    return caught.value


def buy_with(case: str, **data: object) -> dict:
    body = copy.deepcopy(sample(case, "product_buy"))
    body["data"]["states"][0]["data"].update(data)
    return body


# --- обычный путь ---


def test_in_stock_price_from_buy_button() -> None:
    product = parse(FakeBrowser())

    assert (product.title, product.price_minor, product.availability, product.region_code, product.site_name) == (
        "Корпус LIAN LI O11 Vision-M [G99.O11VMW.R0] белый", 679_900, Availability.IN_STOCK, "spb", "DNS"
    )
    assert product.image_url is not None and product.image_url.startswith("https://")


def test_delivery_only_is_in_stock_at_buy_button_price() -> None:
    # Ошибка 13.10.2026: microdata говорит OutOfStock (нет в магазинах города), а купить можно с доставкой.
    product = parse(FakeBrowser(found=responses("delivery_only")), url="https://www.dns-shop.ru/product/d1e6f9e58f48d582/x/")

    assert (product.title, product.price_minor, product.availability) == (
        "Материнская плата GIGABYTE B650E AORUS STEALTH ICE", 3_179_900, Availability.IN_STOCK
    )


def test_unavailable_has_no_price() -> None:
    product = parse(FakeBrowser(found=responses("unavailable")), url="https://www.dns-shop.ru/product/0b6fcc08ca91d9cb/x/")

    assert (product.price_minor, product.availability) == (None, Availability.OUT_OF_STOCK)
    assert product.title == "Оперативная память ADATA Premier OEM [AD4U32008G22-BGN] 8 ГБ"


def test_waits_for_all_three_responses() -> None:
    complete = FakeBrowser()
    without_avails = FakeBrowser(found=responses(avails=None))
    parse(complete)
    with pytest.raises(PriceWatcherError):
        parse(without_avails)

    assert complete.ready_seen == [True] and without_avails.ready_seen == [False]


def test_address_is_reduced_to_product_card() -> None:
    browser = FakeBrowser()

    parse(browser, url=URL + "?utm_source=x&from=search#reviews")

    assert browser.calls == [(URL, "dns")]


def test_city_from_page_settings() -> None:
    assert selected_city(PAGE_HTML) == SPB


# --- граница ---


def test_buy_button_says_not_available_even_if_cart_widget_present() -> None:
    product = parse(FakeBrowser(found=responses(buy=buy_with("in_stock", notAvail=True))))

    assert (product.price_minor, product.availability) == (None, Availability.OUT_OF_STOCK)


def test_without_cart_mark_is_not_available() -> None:
    avails = sample("in_stock", "avails")
    for state in avails["data"]["states"]:
        state["data"]["html"] = state["data"]["html"].replace("data-cart-available", "")
    product = parse(FakeBrowser(found=responses(avails=avails)))

    assert product.availability is Availability.OUT_OF_STOCK


def test_fractional_price_without_float() -> None:
    body = json.dumps(buy_with("in_stock", price={"current": 7799.5}), ensure_ascii=False)

    assert parse(FakeBrowser(found=responses(buy=body))).price_minor == 779_950


# --- отказ ---


def test_other_city_is_region_mismatch() -> None:
    caught = error(FakeBrowser(html=PAGE_HTML.replace(SPB, "30b7c1f3-03fb-11dc-95ee-00151716f9f5")))

    assert caught.code is ErrorCode.REGION_MISMATCH


def test_unknown_region_code() -> None:
    assert error(FakeBrowser(), region="nsk").code is ErrorCode.REGION_MISMATCH


def test_qrator_page_is_blocked() -> None:
    blocked = FakeBrowser(html="<html>qrator</html>", found=responses(microdata=None, buy=None, avails=None), status=401)

    assert error(blocked).code is ErrorCode.BLOCKED


@pytest.mark.parametrize("missing", ["microdata", "buy", "avails"])
def test_missing_response_is_layout_change(missing: str) -> None:
    assert error(FakeBrowser(found=responses(**{missing: None}))).code is ErrorCode.LAYOUT_CHANGED


@pytest.mark.parametrize(
    "microdata",
    ["{broken", {"result": False, "data": None}, {"result": True, "data": {"@type": "Organization"}}, {"result": True}],
)
def test_changed_microdata_is_layout_change(microdata: object) -> None:
    assert error(FakeBrowser(found=responses(microdata=microdata))).code is ErrorCode.LAYOUT_CHANGED


@pytest.mark.parametrize(
    "buy",
    [
        "{broken", "[1, 2]", {"result": True, "data": {"states": []}}, {"result": True, "data": {"states": ["x"]}},
        buy_with("in_stock", price={"current": "по запросу"}), buy_with("in_stock", price=None),
    ],
    ids=["not-json", "list", "no-states", "state-not-object", "price-not-number", "no-price"],
)
def test_changed_buy_state_is_layout_change(buy: object) -> None:
    assert error(FakeBrowser(found=responses(buy=buy))).code is ErrorCode.LAYOUT_CHANGED


def test_changed_avails_is_layout_change() -> None:
    assert error(FakeBrowser(found=responses(avails={"result": True, "data": {"states": [{"data": {}}]}}))).code is ErrorCode.LAYOUT_CHANGED


def test_page_without_city_settings_is_layout_change() -> None:
    assert error(FakeBrowser(html="<html><h1>Корпус</h1></html>")).code is ErrorCode.LAYOUT_CHANGED


def test_foreign_currency() -> None:
    microdata = sample("in_stock", "microdata")
    microdata["data"]["offers"]["priceCurrency"] = "KZT"

    assert error(FakeBrowser(found=responses(microdata=microdata))).code is ErrorCode.UNSUPPORTED_CURRENCY


@pytest.mark.parametrize("raw", ["https://www.dns-shop.ru/", "https://www.dns-shop.ru/catalog/17a89aab16404e77/videokarty/", "https://www.dns-shop.ru/product/xyz/"])
def test_address_without_product_is_invalid(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        canonical_url(normalize_url(raw))

    assert caught.value.code is ErrorCode.INVALID_URL
