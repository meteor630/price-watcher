"""Контракт адаптера магазина (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Адаптер получает приведённый адрес и загрузчик страниц, возвращает `ParsedProduct`
или поднимает `PriceWatcherError` с кодом сбора цен: PRICE_NOT_FOUND, LAYOUT_CHANGED,
BLOCKED, CAPTCHA, TIMEOUT, REGION_MISMATCH, UNSUPPORTED_CURRENCY. Цена не в рублях —
ошибка, поэтому в результате валюты нет: только RUB.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pricewatch.domain.pricing import Availability
from pricewatch.parsers.urls import NormalizedUrl

TITLE_MAX = 500
IMAGE_URL_MAX = 2048


class PriceSource(StrEnum):
    """Откуда взята цена — показывается пользователю рядом с ценой."""

    SITE_DATA = "site_data"  # данные магазина: JSON-LD, meta, JSON страницы
    PAGE = "page"  # найдена «глазами» на странице


@dataclass(frozen=True, slots=True)
class ParsedProduct:
    title: str | None
    price_minor: int | None
    card_price_minor: int | None
    availability: Availability
    image_url: str | None
    # Регион, для которого магазин подтвердил цену.
    region_code: str
    source: PriceSource = PriceSource.SITE_DATA
    # Название сайта для автоматически созданного магазина (og:site_name).
    site_name: str | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.price_minor, self.card_price_minor):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise TypeError("цены — целые копейки")
            if value is not None and value <= 0:
                raise ValueError("цена должна быть положительной")
        if self.availability is not Availability.OUT_OF_STOCK and self.price_minor is None:
            raise ValueError("товар в наличии без цены")
        if self.title is not None and not 0 < len(self.title) <= TITLE_MAX:
            raise ValueError("название пустое или длиннее предела")
        if self.image_url is not None and (not self.image_url.startswith("https://") or len(self.image_url) > IMAGE_URL_MAX):
            raise ValueError("адрес фото — только https и не длиннее предела")


class StoreParser(Protocol):
    """Адаптер одного магазина; каждый живёт в своём файле `parsers/stores/`."""

    key: str
    # Имена магазина без www. и m.; поддомены тоже относятся к нему.
    hosts: frozenset[str]

    def parse(self, url: NormalizedUrl, region_code: str) -> ParsedProduct: ...
