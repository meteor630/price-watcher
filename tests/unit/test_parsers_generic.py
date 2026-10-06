"""pricewatch.parsers.generic: JSON-LD → meta → микроразметка; кандидаты из текста; коды ответа."""

import json
from dataclasses import dataclass, field

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.fetch import FetchResponse
from pricewatch.parsers.generic import GenericParser, check_status, parse_html
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

PAGE = normalize_url("https://shop.example/catalog/lamp-42")


def page(*, json_ld: object | str | None = None, head: str = "", body: str = "") -> str:
    script = ""
    if json_ld is not None:
        content = json_ld if isinstance(json_ld, str) else json.dumps(json_ld, ensure_ascii=False)
        script = f'<script type="application/ld+json">{content}</script>'
    return f"<html><head><title> Лампа — магазин </title>{script}{head}</head><body>{body}</body></html>"


def parse(html: str) -> ParsedProduct:
    return parse_html(html, PAGE, "spb")


def error(html: str) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(html)
    return caught.value


PRODUCT = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Лампа настольная «Ёжик» 40 Вт",
    "image": ["/img/lamp.jpg", "https://cdn.shop.example/lamp-2.jpg"],
    "offers": {"@type": "Offer", "price": "12990.00", "priceCurrency": "RUB", "availability": "https://schema.org/InStock"},
}


# --- JSON-LD ---


def test_json_ld_product() -> None:
    assert parse(page(json_ld=PRODUCT)) == ParsedProduct(
        "Лампа настольная «Ёжик» 40 Вт", 1_299_000, None, Availability.IN_STOCK, "https://shop.example/img/lamp.jpg", "spb"
    )


def test_json_ld_numeric_price_is_read_without_float() -> None:
    html = page(json_ld='{"@type":"Product","name":"Лампа","offers":{"price":12990.10,"priceCurrency":"RUB"}}')

    assert parse(html).price_minor == 1_299_010


def test_json_ld_in_graph_and_type_list() -> None:
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, {**PRODUCT, "@type": ["Product", "Thing"]}]}

    assert parse(page(json_ld=graph)).price_minor == 1_299_000


def test_aggregate_offer_uses_low_price() -> None:
    product = {**PRODUCT, "offers": {"@type": "AggregateOffer", "lowPrice": "9990", "highPrice": "15990", "priceCurrency": "RUB"}}

    assert parse(page(json_ld=product)).price_minor == 999_000


def test_cheapest_in_stock_offer_wins() -> None:
    offers = [
        {"price": "15000", "priceCurrency": "RUB", "availability": "InStock"},
        {"price": "9000", "priceCurrency": "RUB", "availability": "https://schema.org/OutOfStock"},
        {"price": "11000", "priceCurrency": "RUB", "availability": "http://schema.org/InStock"},
    ]

    assert parse(page(json_ld={**PRODUCT, "offers": offers})).price_minor == 1_100_000


def test_price_in_price_specification() -> None:
    product = {**PRODUCT, "offers": {"priceSpecification": {"price": "500", "priceCurrency": "RUB"}}}

    assert parse(page(json_ld=product)).price_minor == 50_000


def test_out_of_stock_without_price() -> None:
    product = {**PRODUCT, "offers": {"availability": "https://schema.org/OutOfStock"}}

    assert parse(page(json_ld=product)) == ParsedProduct(
        "Лампа настольная «Ёжик» 40 Вт", None, None, Availability.OUT_OF_STOCK, "https://shop.example/img/lamp.jpg", "spb"
    )


def test_all_offers_out_of_stock() -> None:
    product = {**PRODUCT, "offers": {**PRODUCT["offers"], "availability": "SoldOut"}}  # type: ignore[dict-item]

    assert parse(page(json_ld=product)).availability is Availability.OUT_OF_STOCK


def test_unknown_availability_with_price() -> None:
    product = {**PRODUCT, "offers": {"price": "100", "priceCurrency": "RUB"}}

    parsed = parse(page(json_ld=product))
    assert (parsed.price_minor, parsed.availability) == (10_000, Availability.UNKNOWN)


def test_foreign_currency_is_refused() -> None:
    product = {**PRODUCT, "offers": {"price": "19.99", "priceCurrency": "USD"}}

    assert error(page(json_ld=product)).code is ErrorCode.UNSUPPORTED_CURRENCY


def test_ruble_offer_wins_over_foreign_one() -> None:
    offers = [{"price": "19.99", "priceCurrency": "USD"}, {"price": "1990", "priceCurrency": "RUB"}]

    assert parse(page(json_ld={**PRODUCT, "offers": offers})).price_minor == 199_000


def test_broken_json_ld_block_does_not_stop_meta() -> None:
    head = '<meta property="product:price:amount" content="777"><meta property="product:price:currency" content="RUB">'

    assert parse(page(json_ld='{"@type": "Product", broken', head=head)).price_minor == 77_700


# --- meta и микроразметка ---


def test_open_graph_meta() -> None:
    head = (
        '<meta property="og:title" content="Лампа OG"><meta property="og:image" content="https://cdn.shop.example/og.jpg">'
        '<meta property="product:price:amount" content="1 299,50"><meta property="product:price:currency" content="RUB">'
        '<meta property="product:availability" content="in stock">'
    )

    assert parse(page(head=head)) == ParsedProduct("Лампа OG", 129_950, None, Availability.IN_STOCK, "https://cdn.shop.example/og.jpg", "spb")


def test_microdata() -> None:
    body = (
        '<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Лампа</h1>'
        '<meta itemprop="price" content="4990"><meta itemprop="priceCurrency" content="RUB">'
        '<link itemprop="availability" href="https://schema.org/InStock"></div>'
    )

    parsed = parse(page(body=body))
    assert (parsed.price_minor, parsed.availability, parsed.title) == (499_000, Availability.IN_STOCK, "Лампа — магазин")


def test_title_falls_back_to_page_title_and_is_cut() -> None:
    long_title = "Т" * 600
    html = f'<html><head><title>{long_title}</title><meta itemprop="price" content="10"></head></html>'

    assert parse(html).title == "Т" * 500


def test_image_must_be_https() -> None:
    product = {**PRODUCT, "image": "http://cdn.shop.example/lamp.jpg"}

    assert parse(page(json_ld=product)).image_url is None


def test_image_object() -> None:
    product = {**PRODUCT, "image": {"@type": "ImageObject", "url": "https://cdn.shop.example/obj.jpg"}}

    assert parse(page(json_ld=product)).image_url == "https://cdn.shop.example/obj.jpg"


# --- цены нет ---


def test_price_on_request_gives_text_candidates() -> None:
    product = {**PRODUCT, "offers": {"price": "по запросу"}}
    body = "<p>Артикул 123456</p><span>Цена: 12 990 ₽</span><span>Было 14 990 ₽</span><script>var p = '1 ₽';</script>"

    caught = error(page(json_ld=product, body=body))

    assert caught.code is ErrorCode.PRICE_NOT_FOUND
    assert caught.details["candidates"] == [1_299_000, 1_499_000]  # текст скриптов не смотрится


def test_page_without_any_data() -> None:
    caught = error("<html><body><h1>Страница не найдена</h1></body></html>")

    assert (caught.code, caught.details["candidates"]) == (ErrorCode.PRICE_NOT_FOUND, [])


def test_garbage_markup_does_not_crash() -> None:
    caught = error("<<<html><meta property=><script type='application/ld+json'>[[[</scr")

    assert caught.code is ErrorCode.PRICE_NOT_FOUND


# --- коды ответа и загрузка ---


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, ErrorCode.PRICE_NOT_FOUND), (410, ErrorCode.PRICE_NOT_FOUND), (403, ErrorCode.BLOCKED), (429, ErrorCode.BLOCKED),
     (401, ErrorCode.BLOCKED), (500, ErrorCode.TIMEOUT), (503, ErrorCode.TIMEOUT), (302, ErrorCode.LAYOUT_CHANGED),
     (204, ErrorCode.LAYOUT_CHANGED)],
)
def test_status_codes(status: int, code: ErrorCode) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        check_status(FetchResponse(status, PAGE, "text/html", b""))

    assert caught.value.code is code


@dataclass
class FakeFetcher:
    response: FetchResponse
    calls: list[NormalizedUrl] = field(default_factory=list)

    def fetch(self, url: NormalizedUrl, *, accept: str = "text/html") -> FetchResponse:
        self.calls.append(url)
        return self.response


def test_generic_parser_fetches_and_parses_final_url() -> None:
    final = normalize_url("https://www.shop.example/catalog/lamp-42/")
    fetcher = FakeFetcher(FetchResponse(200, final, "text/html; charset=windows-1251", page(json_ld=PRODUCT).encode("cp1251")))

    parsed = GenericParser(fetcher).parse(PAGE, "spb")

    assert fetcher.calls == [PAGE]
    assert parsed.title == "Лампа настольная «Ёжик» 40 Вт"
    assert parsed.image_url == "https://www.shop.example/img/lamp.jpg"  # относительный адрес — от итоговой страницы


def test_generic_parser_refuses_non_html() -> None:
    fetcher = FakeFetcher(FetchResponse(200, PAGE, "application/pdf", b"%PDF"))

    with pytest.raises(PriceWatcherError) as caught:
        GenericParser(fetcher).parse(PAGE, "spb")

    assert caught.value.code is ErrorCode.LAYOUT_CHANGED


def test_json_ld_price_is_not_mixed_with_other_products_microdata() -> None:
    # Регард, 06.10.2026: в JSON-LD — сам товар за 42 100 ₽, а в микроразметке ниже — рекомендованный
    # товар за 14 340 ₽ («нет в наличии» у другого). Источники идут по очереди, а не смешиваются.
    product = {**PRODUCT, "offers": {"price": "42100.00", "priceCurrency": "RUB", "availability": "https://schema.org/InStock"}}
    body = '<div itemscope><meta itemprop="price" content="14340"><link itemprop="availability" href="https://schema.org/InStock"></div>'

    assert parse(page(json_ld=product, body=body)).price_minor == 4_210_000


def test_json_ld_out_of_stock_is_not_overridden_by_other_products_microdata() -> None:
    product = {**PRODUCT, "offers": {"availability": "https://schema.org/OutOfStock"}}
    body = '<div itemscope><meta itemprop="price" content="14340"></div>'

    assert parse(page(json_ld=product, body=body)).availability is Availability.OUT_OF_STOCK


def test_related_products_nested_in_product_are_ignored() -> None:
    # Регард, 06.10.2026: внутри JSON-LD товара — похожие товары со своими предложениями.
    related = {"@type": "Product", "name": "Другой процессор", "offers": {"price": "14340", "priceCurrency": "RUB", "availability": "InStock"}}
    product = {**PRODUCT, "offers": {"price": "42100.00", "priceCurrency": "RUB", "availability": "InStock"}, "isSimilarTo": [related]}

    assert parse(page(json_ld=product)).price_minor == 4_210_000


def test_product_group_variants_are_read() -> None:
    variant = {"@type": "Product", "name": "Лампа белая", "offers": {"price": "990", "priceCurrency": "RUB", "availability": "InStock"}}
    group = {"@context": "https://schema.org", "@type": "ProductGroup", "name": "Лампа", "hasVariant": [variant]}

    assert parse(page(json_ld=group)).price_minor == 99_000
