"""Адаптер Регарда на сохранённой странице: цена из JSON-LD, город из __NEXT_DATA__, cookie города.

Образец tests/fixtures/stores/regard/product_spb.html — настоящая страница от 06.10.2026, сокращённая:
оба блока JSON-LD как есть (в блоке товара — похожие товары со своими ценами) и состояние города.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.fetch import FetchResponse
from pricewatch.parsers.stores.regard import RegardParser, selected_town
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

SAMPLE = (Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "regard" / "product_spb.html").read_text(encoding="utf-8")
URL = normalize_url("https://www.regard.ru/product/718588/processor-amd-ryzen-7-9800x3d-oem")


@dataclass
class FakeFetcher:
    html: str = SAMPLE
    status: int = 200
    calls: list[tuple[str, dict | None]] = field(default_factory=list)

    def fetch(self, url: NormalizedUrl, *, accept: str = "text/html", cookies: dict | None = None) -> FetchResponse:
        self.calls.append((url.url, cookies))
        return FetchResponse(self.status, url, "text/html; charset=utf-8", self.html.encode())


def parse(fetcher: FakeFetcher, url: NormalizedUrl = URL, region: str = "spb") -> ParsedProduct:
    return RegardParser(fetcher).parse(url, region)


def error(fetcher: FakeFetcher, **kwargs: object) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(fetcher, **kwargs)  # type: ignore[arg-type]
    return caught.value


def test_product_from_saved_page_ignores_similar_products() -> None:
    assert parse(FakeFetcher()) == ParsedProduct(
        "Процессор AMD Ryzen 7 9800X3D OEM",
        4_210_000,  # не 14 340 ₽ похожего товара из того же блока JSON-LD
        None,
        Availability.IN_STOCK,
        "https://www.regard.ru/api/site/cacheimg/goods/6220912/1200",
        "spb",
    )


def test_city_cookie_is_sent() -> None:
    fetcher = FakeFetcher()

    parse(fetcher)

    assert fetcher.calls == [(URL.url, {"city_id": "137"})]


def test_town_from_page_state() -> None:
    assert selected_town(SAMPLE) == 137


def test_page_for_other_city_is_region_mismatch() -> None:
    caught = error(FakeFetcher(SAMPLE.replace('"cookieTownId": 137', '"cookieTownId": 44')))

    assert (caught.code, caught.details["town"]) == (ErrorCode.REGION_MISMATCH, 44)


def test_unknown_region_code() -> None:
    assert error(FakeFetcher(), region="nsk").code is ErrorCode.REGION_MISMATCH


@pytest.mark.parametrize(
    "html",
    [
        SAMPLE.replace('id="__NEXT_DATA__"', 'id="other"'),  # нет состояния страницы
        SAMPLE.replace('"cookieTownId": 137', '"cookieTown": 137'),  # нет города
        SAMPLE.replace('<script id="__NEXT_DATA__" type="application/json">{', '<script id="__NEXT_DATA__" type="application/json">{{'),
    ],
)
def test_changed_page_state_is_layout_change(html: str) -> None:
    assert error(FakeFetcher(html)).code is ErrorCode.LAYOUT_CHANGED


def test_out_of_stock() -> None:
    html = SAMPLE.replace('"availability":"https://schema.org/InStock","price":"42100.00"', '"availability":"https://schema.org/OutOfStock"', 1)

    assert parse(FakeFetcher(html)).availability is Availability.OUT_OF_STOCK


@pytest.mark.parametrize(("status", "code"), [(404, ErrorCode.PRICE_NOT_FOUND), (403, ErrorCode.BLOCKED), (503, ErrorCode.TIMEOUT)])
def test_store_answer_codes(status: int, code: ErrorCode) -> None:
    assert error(FakeFetcher(status=status)).code is code


@pytest.mark.parametrize("raw", ["https://www.regard.ru/", "https://www.regard.ru/catalog/1001/processory", "https://www.regard.ru/product/abc"])
def test_address_without_product_is_invalid(raw: str) -> None:
    fetcher = FakeFetcher()

    assert error(fetcher, url=normalize_url(raw)).code is ErrorCode.INVALID_URL
    assert fetcher.calls == []
