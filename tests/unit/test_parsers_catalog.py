"""pricewatch.parsers.catalog: известные магазины — к карточке по своим правилам, остальные — общему парсеру."""

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.catalog import AVITO_DELAY_MS, BROWSER_DELAY_MS, HTTP_DELAY_MS, KNOWN_STORES, LinkCatalog, StoreLink

CATALOG = LinkCatalog()


@pytest.mark.parametrize(
    ("raw", "url", "key"),
    [
        ("https://m.avito.ru/moskva/tovary_dlya_kompyutera/klaviatura_3518455623?context=x", "https://www.avito.ru/moskva/tovary_dlya_kompyutera/klaviatura_3518455623", "avito"),
        ("https://www.ozon.ru/search/?text=x&product_id=3208684872", "https://www.ozon.ru/product/3208684872/", "ozon"),
        ("https://market.yandex.ru/card/hagibis/5765014011?nid=1&cpc=x", "https://market.yandex.ru/card/hagibis/5765014011", "yandex_market"),
        ("https://wildberries.ru/catalog/1136432918/detail.aspx?targetUrl=GP", "https://www.wildberries.ru/catalog/1136432918/detail.aspx", "wildberries"),
        ("https://www.regard.ru/product/718588/processor-amd?x=1", "https://www.regard.ru/product/718588/processor-amd", "regard"),
        ("https://www.dns-shop.ru/product/941bee591c43d582/korpus/?p=2", "https://www.dns-shop.ru/product/941bee591c43d582/korpus/", "dns"),
    ],
)
def test_known_store_link_reduced_to_card(raw: str, url: str, key: str) -> None:
    link = CATALOG.resolve(raw)

    assert (link.normalized_url, link.parser_key, link.auto_created) == (url, key, False)


def test_known_store_data() -> None:
    assert CATALOG.resolve("https://www.ozon.ru/product/3208684872/") == StoreLink(
        "https://www.ozon.ru/product/3208684872/", "ozon.ru", "ozon", "Ozon", True, BROWSER_DELAY_MS, False
    )
    assert CATALOG.resolve("https://www.avito.ru/moskva/a/b_3518455623").min_delay_ms == AVITO_DELAY_MS


def test_unknown_site_goes_to_universal_parser() -> None:
    assert CATALOG.resolve("https://www.onlinetrade.ru/catalogue/x-123.html?utm_source=a") == StoreLink(
        "https://www.onlinetrade.ru/catalogue/x-123.html", "onlinetrade.ru", "universal", "onlinetrade.ru", False, HTTP_DELAY_MS, True
    )


@pytest.mark.parametrize("raw", ["https://ozon.ru.evil.example/product/1/", "https://notozon.ru/product/3208684872/"])
def test_lookalike_host_is_not_known_store(raw: str) -> None:
    assert CATALOG.resolve(raw).parser_key == "universal"


@pytest.mark.parametrize(
    "raw",
    ["https://www.ozon.ru/category/x-1/", "https://www.dns-shop.ru/", "https://www.wildberries.ru/brands/x", "http://shop.example/p", "https://127.0.0.1/"],
)
def test_bad_or_non_product_address_is_invalid(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        CATALOG.resolve(raw)

    assert caught.value.code is ErrorCode.INVALID_URL


def test_every_known_store_has_unique_host_and_key() -> None:
    assert len({store.host for store in KNOWN_STORES}) == len({store.key for store in KNOWN_STORES}) == len(KNOWN_STORES)
