"""Проверка и приведение адресов товаров к одному виду (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Разрешено только `https` без логина и пароля, на стандартном порту и по имени:
IP-адрес в любой записи (127.0.0.1, 2130706433, 0x7f.1, [::1]) отклоняется — так
адрес не обойдёт защиту от SSRF до разрешения имени. Имя приводится к нижнему
регистру и punycode, фрагмент и метки рекламы убираются, параметры сортируются:
одна страница товара даёт одну строку каталога ссылок.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from pricewatch.domain.errors import ErrorCode, PriceWatcherError

URL_MAX = 2048
HOST_MAX = 253
# Метки рекламы и источников перехода: на цену не влияют, но плодят дубли ссылок.
TRACKING_PARAMS = frozenset({"gclid", "yclid", "fbclid", "_openstat", "ysclid", "utm_referrer"})
TRACKING_PREFIXES = ("utm_",)
# Префиксы имени, которые ведут на тот же магазин.
STORE_HOST_PREFIXES = ("www.", "m.")
LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
# По правилам WHATWG имя с числовой последней частью браузер читает как IPv4 (0x7f.1, 2130706433).
NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$")
# Разрешённые в пути и параметрах символы, остальное кодируется одинаково для любых исходных записей.
SAFE_PATH = "/:@!$&'()*+,;=-._~%"


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    url: str
    host: str
    # Имя магазина без www. и m.: по нему реестр находит адаптер.
    store_host: str


def normalize_url(raw: str) -> NormalizedUrl:
    """Адрес товара в едином виде или INVALID_URL."""
    text = raw.strip()
    if not text or len(text) > URL_MAX or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text):
        raise _invalid()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as error:
        raise _invalid() from error
    if parts.scheme.lower() != "https" or parts.username is not None or parts.password is not None:
        raise _invalid()
    if port not in (None, 443) or parts.hostname is None:
        raise _invalid()
    host = _host(parts.hostname)
    path = quote(parts.path or "/", safe=SAFE_PATH)
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in TRACKING_PARAMS and not key.lower().startswith(TRACKING_PREFIXES)
        ),
        quote_via=quote,
    )
    url = urlunsplit(("https", host, path, query, ""))
    if len(url) > URL_MAX:
        raise _invalid()
    return NormalizedUrl(url, host, store_host(host))


def store_host(host: str) -> str:
    for prefix in STORE_HOST_PREFIXES:
        if host.startswith(prefix) and host.count(".") > 1:
            return host.removeprefix(prefix)
    return host


def _host(hostname: str) -> str:
    if ":" in hostname or "[" in hostname:
        raise _invalid()  # IPv6 в квадратных скобках
    try:
        host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise _invalid() from error
    labels = host.split(".")
    if len(host) > HOST_MAX or len(labels) < 2 or not all(LABEL.match(label) for label in labels):
        raise _invalid()
    if NUMERIC_LABEL.match(labels[-1]):
        raise _invalid()
    return host


def _invalid() -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.INVALID_URL)
