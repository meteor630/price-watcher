"""Справочник магазинов сборщика цен: по адресу — приведённая ссылка и магазин.

Для магазина со своим адаптером адрес приводится к карточке товара его правилами (параметры,
мобильная версия, ссылки из поиска — к одной ссылке каталога), а данные магазина известны заранее.
Любой другой сайт разбирает общий парсер; магазин для него создаётся сам (решение 06.10.2026),
название — имя сайта, позже его уточнит og:site_name.

Пауза между запросами к магазину по умолчанию (решение 06.10.2026): 2 с без браузера,
5 с в браузере (worker добавляет к ней случайные 0–4 с), 60 с для Авито.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pricewatch.parsers.stores import avito, dns, ozon, regard, wildberries, yandex_market
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

HTTP_DELAY_MS = 2_000
BROWSER_DELAY_MS = 5_000
AVITO_DELAY_MS = 60_000
UNIVERSAL = "universal"


@dataclass(frozen=True, slots=True)
class KnownStore:
    key: str
    host: str
    name: str
    has_card_price: bool
    min_delay_ms: int
    canonical: Callable[[NormalizedUrl], NormalizedUrl]


KNOWN_STORES = (
    KnownStore(wildberries.KEY, "wildberries.ru", "Wildberries", False, BROWSER_DELAY_MS, wildberries.canonical_url),
    KnownStore(regard.KEY, "regard.ru", "Регард", False, HTTP_DELAY_MS, regard.canonical_url),
    KnownStore(dns.KEY, "dns-shop.ru", "DNS", False, BROWSER_DELAY_MS, dns.canonical_url),
    KnownStore(ozon.KEY, "ozon.ru", "Ozon", True, BROWSER_DELAY_MS, ozon.canonical_url),
    KnownStore(yandex_market.KEY, "market.yandex.ru", "Яндекс Маркет", True, HTTP_DELAY_MS, yandex_market.canonical_url),
    KnownStore(avito.KEY, "avito.ru", "Авито", False, AVITO_DELAY_MS, avito.canonical_url),
)


@dataclass(frozen=True, slots=True)
class StoreLink:
    normalized_url: str
    store_host: str
    parser_key: str
    store_name: str
    has_card_price: bool
    min_delay_ms: int
    auto_created: bool


class LinkCatalog:
    def __init__(self, known: tuple[KnownStore, ...] = KNOWN_STORES) -> None:
        self._by_host = {store.host: store for store in known}

    def resolve(self, raw_url: str) -> StoreLink:
        url = normalize_url(raw_url)
        known = self._known(url.store_host)
        if known is None:
            return StoreLink(url.url, url.store_host, UNIVERSAL, url.store_host[:100], False, HTTP_DELAY_MS, True)
        card = known.canonical(url)
        return StoreLink(card.url, known.host, known.key, known.name, known.has_card_price, known.min_delay_ms, False)

    def _known(self, store_host: str) -> KnownStore | None:
        labels = store_host.split(".")
        # Поддомен магазина — тот же магазин (m.avito.ru, spb.regard.ru), похожее имя — нет.
        for start in range(len(labels) - 1):
            known = self._by_host.get(".".join(labels[start:]))
            if known is not None:
                return known
        return None
