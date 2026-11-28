"""Что сервер может заставить домашний worker открыть (слой 1 изоляции worker, DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Исходим из того, что сервер может быть захвачен и выдавать любые ссылки. Поэтому перед любой проверкой worker сам,
без слов сервера, решает, можно ли идти по адресу задания:
- домен — из локального списка allowlist.txt на домашнем узле (сам домен или его поддомен). Сервер списка не видит
  и изменить его не может; добавляет домен владелец: python -m pricewatch.worker.allow add <домен>. Список читается
  на каждом задании — добавленный домен действует без перезапуска. Нет файла — ни одного задания (fail closed);
- адрес — только https на 443 (normalize_url), и все адреса имени после резолва публичные: не 10/8, 172.16/12,
  192.168/16, 100.64/10, 127/8, 169.254/16, IPv6 ULA и link-local (is_public_address).
Само соединение с проверенным адресом держат SafeFetcher и прокси браузера (EgressProxy): они резолвят имя один раз
и соединяются ровно с ним, поэтому подмена DNS после этой проверки не помогает.
"""

import logging
from collections.abc import Iterable
from pathlib import Path

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.egress import public_address
from pricewatch.parsers.fetch import Resolver, system_resolver
from pricewatch.parsers.urls import normalize_url

logger = logging.getLogger(__name__)


class AllowlistUnusable(Exception):
    """Файла списка доменов нет, он не читается или в нём не домен: worker не берёт заданий."""


def read_domains(path: Path) -> frozenset[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AllowlistUnusable(str(path)) from error
    try:
        return frozenset(clean_domain(line) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))
    except ValueError as error:
        raise AllowlistUnusable(f"{path}: {error}") from error


def clean_domain(raw: str) -> str:
    """Домен в едином виде (без www., в нижнем регистре); не домен — ValueError."""
    try:
        host = normalize_url(f"https://{raw.strip()}/").host
    except PriceWatcherError:
        raise ValueError(f"не домен: {raw.strip()[:100]}") from None
    return host.removeprefix("www.")


def allowed(host: str, domains: Iterable[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


class EgressGate:
    def __init__(self, allowlist: Path, resolver: Resolver = system_resolver) -> None:
        self._allowlist = allowlist
        self._resolver = resolver

    def check(self, url: str) -> None:
        """Пропустить адрес задания или PriceWatcherError: DOMAIN_NOT_ALLOWED — домена нет в списке,
        INVALID_URL — не https:443 или имя ведёт в частную сеть. AllowlistUnusable — списка нет вовсе."""
        domains = read_domains(self._allowlist)
        host = normalize_url(url).host
        if not allowed(host, domains):
            logger.error("Домен не из списка домашнего узла — задание не выполняется", extra={"host": host})
            raise PriceWatcherError(ErrorCode.DOMAIN_NOT_ALLOWED)
        if public_address(host, self._resolver) is None:
            logger.error("Имя ведёт не в публичный интернет — задание не выполняется", extra={"host": host})
            raise PriceWatcherError(ErrorCode.INVALID_URL)
