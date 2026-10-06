"""pricewatch.parsers.urls: только https по имени, единый вид адреса, отказ на опасных записях."""

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.urls import URL_MAX, NormalizedUrl, normalize_url, store_host


@pytest.mark.parametrize(
    ("raw", "url"),
    [
        ("https://www.wildberries.ru/catalog/141217830/detail.aspx", "https://www.wildberries.ru/catalog/141217830/detail.aspx"),
        ("  HTTPS://WWW.Ozon.RU/product/lampa-123/  ", "https://www.ozon.ru/product/lampa-123/"),
        ("https://www.ozon.ru:443/product/1/", "https://www.ozon.ru/product/1/"),
        ("https://example.ru", "https://example.ru/"),
        ("https://example.ru/item#reviews", "https://example.ru/item"),
        ("https://example.ru.", "https://example.ru/"),
        # метки рекламы убраны, остальные параметры отсортированы
        ("https://example.ru/p?utm_source=x&b=2&a=1&yclid=7&UTM_Medium=y", "https://example.ru/p?a=1&b=2"),
        ("https://example.ru/p?q=лампа ночник".replace(" ", "%20"), "https://example.ru/p?q=%D0%BB%D0%B0%D0%BC%D0%BF%D0%B0%20%D0%BD%D0%BE%D1%87%D0%BD%D0%B8%D0%BA"),
        ("https://example.ru/товар", "https://example.ru/%D1%82%D0%BE%D0%B2%D0%B0%D1%80"),
        ("https://магазин.рф/item", "https://xn--80aairftm.xn--p1ai/item"),
    ],
)
def test_normalized_form(raw: str, url: str) -> None:
    assert normalize_url(raw).url == url


def test_same_page_written_differently_gives_one_url() -> None:
    variants = [
        "https://example.ru/p?b=2&a=1",
        "HTTPS://EXAMPLE.RU:443/p?a=1&b=2#top",
        "https://example.ru/p?a=1&utm_campaign=sale&b=2",
        "https://example.ru/p?a=%31&b=2",
    ]

    assert {normalize_url(raw).url for raw in variants} == {"https://example.ru/p?a=1&b=2"}


@pytest.mark.parametrize(
    ("raw", "host", "store"),
    [
        ("https://www.ozon.ru/p/1/", "www.ozon.ru", "ozon.ru"),
        ("https://m.ozon.ru/p/1/", "m.ozon.ru", "ozon.ru"),
        ("https://spb.regard.ru/p/1", "spb.regard.ru", "spb.regard.ru"),
        ("https://m.ru/p", "m.ru", "m.ru"),  # «m.» здесь — само имя, а не мобильная версия
    ],
)
def test_store_host_drops_www_and_mobile(raw: str, host: str, store: str) -> None:
    assert normalize_url(raw) == NormalizedUrl(normalize_url(raw).url, host, store)


def test_store_host_helper() -> None:
    assert store_host("www.dns-shop.ru") == "dns-shop.ru"


@pytest.mark.parametrize(
    "raw",
    [
        "http://example.ru/p",  # только https
        "ftp://example.ru/p",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "//example.ru/p",
        "example.ru/p",
        "https://user:pass@example.ru/p",  # логин и пароль в адресе
        "https://user@example.ru/p",
        "https://example.ru:8443/p",  # нестандартный порт
        "https://example.ru:0/p",
        "https://example.ru:99999/p",
        "https://127.0.0.1/p",  # IP вместо имени — в любой записи
        "https://2130706433/p",
        "https://0x7f000001/p",
        "https://0x7f.1/p",
        "https://127.1/p",
        "https://10.0.0.1/p",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/p",
        "https://[::ffff:127.0.0.1]/p",
        "https://localhost/p",  # имя без точки
        "https://-bad.ru/p",
        "https://bad_name.ru/p",
        "https://example.ru/p\nHost: evil",
        "https://example.ru/ p",
        "https://exa\tmple.ru/p",
        "",
        "   ",
        "https://",
        "https://" + "a" * 64 + ".ru/p",  # часть имени длиннее 63
        "https://example.ru/" + "a" * URL_MAX,
    ],
)
def test_rejected(raw: str) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        normalize_url(raw)

    assert caught.value.code is ErrorCode.INVALID_URL


def test_longest_allowed_url() -> None:
    raw = "https://example.ru/" + "a" * (URL_MAX - len("https://example.ru/"))

    assert len(normalize_url(raw).url) == URL_MAX
