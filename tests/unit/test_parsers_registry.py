"""pricewatch.parsers.registry и contract: магазин по имени сайта, результат адаптера."""

from dataclasses import dataclass

import pytest

from pricewatch.domain.pricing import Availability
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.registry import Registry
from pricewatch.parsers.urls import NormalizedUrl, normalize_url


@dataclass(frozen=True)
class FakeParser:
    key: str
    hosts: frozenset[str]

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct:
        return ParsedProduct("Товар", 100, None, Availability.IN_STOCK, None, region_code)


WB = FakeParser("wildberries", frozenset({"wildberries.ru", "wb.ru"}))
REGARD = FakeParser("regard", frozenset({"regard.ru"}))
REGISTRY = Registry([WB, REGARD])


@pytest.mark.parametrize(
    ("raw", "parser"),
    [
        ("https://www.wildberries.ru/catalog/1/detail.aspx", WB),
        ("https://wildberries.ru/catalog/1/detail.aspx", WB),
        ("https://m.wildberries.ru/catalog/1/detail.aspx", WB),
        ("https://global.wildberries.ru/catalog/1", WB),  # поддомен магазина
        ("https://wb.ru/p", WB),
        ("https://spb.regard.ru/product/1", REGARD),
    ],
)
def test_store_found_by_host(raw: str, parser: FakeParser) -> None:
    assert REGISTRY.parser_for(normalize_url(raw)) is parser


@pytest.mark.parametrize(
    "raw",
    [
        "https://notwildberries.ru/p",  # похожее имя
        "https://wildberries.ru.evil.com/p",  # имя магазина внутри чужого
        "https://wildberries.com/p",
        "https://www.ozon.ru/p",  # магазин не подключён
    ],
)
def test_unknown_or_lookalike_host_not_matched(raw: str) -> None:
    assert REGISTRY.parser_for(normalize_url(raw)) is None


def test_bare_zone_never_matches() -> None:
    registry = Registry([FakeParser("zone", frozenset({"ru"}))])

    assert registry.parser_for(normalize_url("https://example.ru/p")) is None


def test_two_parsers_cannot_claim_one_host() -> None:
    with pytest.raises(ValueError):
        Registry([WB, FakeParser("copy", frozenset({"wb.ru"}))])


def test_empty_registry_finds_nothing() -> None:
    assert Registry([]).parser_for(normalize_url("https://wildberries.ru/p")) is None


# --- результат адаптера ---


def test_parsed_product_normal_and_out_of_stock() -> None:
    assert ParsedProduct("Лампа", 129_900, 119_900, Availability.IN_STOCK, "https://img.example.ru/1.jpg", "spb").price_minor == 129_900
    assert ParsedProduct("Лампа", None, None, Availability.OUT_OF_STOCK, None, "spb").price_minor is None


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"price_minor": 0}, ValueError),
        ({"price_minor": 1299.0}, TypeError),
        ({"card_price_minor": -1}, ValueError),
        ({"price_minor": None}, ValueError),  # в наличии без цены
        ({"title": ""}, ValueError),
        ({"title": "т" * 501}, ValueError),
        ({"image_url": "http://img.example.ru/1.jpg"}, ValueError),
        ({"image_url": "https://img.example.ru/" + "a" * 2048}, ValueError),
    ],
)
def test_parsed_product_rejects_bad_values(kwargs: dict, error: type[Exception]) -> None:
    values = {"title": "Лампа", "price_minor": 100, "card_price_minor": None, "availability": Availability.IN_STOCK,
              "image_url": None, "region_code": "spb"} | kwargs
    with pytest.raises(error):
        ParsedProduct(**values)


def test_title_at_limit_is_accepted() -> None:
    assert ParsedProduct("т" * 500, 100, None, Availability.IN_STOCK, None, "spb").title == "т" * 500
