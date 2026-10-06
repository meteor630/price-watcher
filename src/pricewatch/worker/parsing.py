"""Парсеры worker: адаптер по ключу магазина из задания, для остальных сайтов — общий парсер.

Браузер один на процесс и запускается при первом магазине, которому он нужен. Способ, сработавший
для сайта (быстрый запрос или браузер), сервер помнит у магазина и присылает в задании; общий
парсер начинает с него и сообщает, какой сработал на этот раз.
"""

from dataclasses import dataclass
from pathlib import Path

from pricewatch.parsers.browser import BrowserFetcher
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.fetch import SafeFetcher
from pricewatch.parsers.stores.avito import AvitoParser
from pricewatch.parsers.stores.dns import DnsParser
from pricewatch.parsers.stores.ozon import OzonParser
from pricewatch.parsers.stores.regard import RegardParser
from pricewatch.parsers.stores.wildberries import WildberriesParser
from pricewatch.parsers.stores.yandex_market import YandexMarketParser
from pricewatch.parsers.universal import InMemoryMethods, UniversalParser
from pricewatch.parsers.urls import normalize_url


@dataclass(frozen=True, slots=True)
class Parsed:
    product: ParsedProduct
    # Способ, сработавший у общего парсера; у адаптеров магазинов он свой и не сообщается.
    fetch_method: str | None


class Parsers:
    def __init__(self, profiles_dir: Path) -> None:
        self._fetcher = SafeFetcher()
        self._browser = BrowserFetcher(profiles_dir)
        self._adapters = {
            "wildberries": WildberriesParser(self._browser),
            "regard": RegardParser(self._fetcher),
            "dns": DnsParser(self._browser),
            "ozon": OzonParser(self._browser),
            "yandex_market": YandexMarketParser(self._fetcher, self._browser),
            "avito": AvitoParser(self._browser),
        }

    def close(self) -> None:
        self._browser.close()
        self._fetcher.close()

    def parse(self, url: str, region_code: str, parser_key: str, store_host: str, fetch_method: str | None) -> Parsed:
        normalized = normalize_url(url)
        adapter = self._adapters.get(parser_key)
        if adapter is not None:
            return Parsed(adapter.parse(normalized, region_code), None)
        methods = InMemoryMethods()
        if fetch_method is not None:
            methods.remember(store_host, fetch_method)
        product = UniversalParser(self._fetcher, self._browser, methods).parse(normalized, region_code)
        return Parsed(product, methods.get(store_host))
