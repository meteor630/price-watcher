"""Реестр адаптеров: магазин по имени сайта (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Поддомен относится к магазину (`spb.example.ru` → `example.ru`), но не наоборот,
и похожее имя (`example.ru.evil.com`, `notexample.ru`) не совпадает.
"""

from collections.abc import Iterable

from pricewatch.parsers.contract import StoreParser
from pricewatch.parsers.urls import NormalizedUrl


class Registry:
    def __init__(self, parsers: Iterable[StoreParser]) -> None:
        self._by_host: dict[str, StoreParser] = {}
        for parser in parsers:
            for host in parser.hosts:
                if host in self._by_host:
                    raise ValueError(f"имя {host} заявлено двумя адаптерами")
                self._by_host[host] = parser

    def parser_for(self, url: NormalizedUrl) -> StoreParser | None:
        labels = url.store_host.split(".")
        # От полного имени к родительским, но не короче двух частей: «ru» магазином не бывает.
        for start in range(len(labels) - 1):
            parser = self._by_host.get(".".join(labels[start:]))
            if parser is not None:
                return parser
        return None
