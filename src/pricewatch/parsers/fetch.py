"""Безопасная загрузка страниц магазинов (DEVELOPMENT_ARCHITECTURE.md, раздел 7, «Защита от SSRF»).

Worker стоит в домашней сети, поэтому каждый шаг загрузки проверяется:

- адрес приведён `normalize_url`: только https по имени, без логина и порта;
- имя разрешается один раз на шаг, и все полученные адреса должны быть публичными;
  соединение идёт на проверенный адрес, а сертификат проверяется по имени магазина
  (SNI): подмена DNS между проверкой и соединением (rebinding) ничего не даёт;
- перенаправления выполняются вручную, каждое проходит те же проверки, не больше пяти;
- ответ читается потоком: предел размера считается после распаковки, есть общий
  предел времени — медленная отдача по байту не держит worker бесконечно;
- переменные окружения с прокси игнорируются, соединения не переиспользуются между
  магазинами с одним адресом.

Подход — по httpx-secure (github.com/Zaczero/pkgs, 0BSD), реализация своя на httpx2.
"""

import ipaddress
import socket
import time
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import httpx2

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
TOTAL_TIMEOUT_S = 20.0
TIMEOUT = httpx2.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
COOKIE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
# Хорошо известный префикс NAT64: внутри лежит IPv4, который и нужно проверять.
NAT64 = ipaddress.ip_network("64:ff9b::/96")

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], Iterable[IpAddress]]


@dataclass(frozen=True, slots=True)
class FetchResponse:
    status: int
    url: NormalizedUrl
    content_type: str
    body: bytes

    def text(self) -> str:
        """Текст в кодировке из Content-Type, по умолчанию UTF-8; битые байты заменяются."""
        charset = "utf-8"
        for part in self.content_type.split(";")[1:]:
            name, _, value = part.strip().partition("=")
            if name.lower() == "charset" and value:
                charset = value.strip("\"'")
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class Fetcher(Protocol):
    """Загрузчик страниц для адаптеров; рабочая реализация — SafeFetcher."""

    def fetch(self, url: NormalizedUrl, *, accept: str = ..., cookies: Mapping[str, str] | None = ...) -> FetchResponse: ...


def _cookie_header(cookies: Mapping[str, str]) -> str:
    # Значения задаёт код адаптера, но разделители и переводы строк всё равно не пропускаются.
    for name, value in cookies.items():
        if not COOKIE_TOKEN.match(name) or not COOKIE_TOKEN.match(value):
            raise ValueError("недопустимая cookie")
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def is_public_address(ip: IpAddress) -> bool:
    """Адрес в интернете, а не в домашней, служебной или многоадресной сети."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_address(ip.ipv4_mapped)
        if ip in NAT64:
            return is_public_address(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return ip.is_global and not ip.is_multicast


def system_resolver(host: str) -> list[IpAddress]:
    return [
        ipaddress.ip_address(info[4][0])
        for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    ]


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx2.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_bytes: int = MAX_BYTES,
        total_timeout_s: float = TOTAL_TIMEOUT_S,
    ) -> None:
        self._resolver = resolver
        self._clock = clock
        self._max_bytes = max_bytes
        self._total_timeout_s = total_timeout_s
        self._client = httpx2.Client(
            transport=transport,
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=False,  # прокси из окружения увели бы запрос мимо проверок
            limits=httpx2.Limits(max_keepalive_connections=0),
        )

    def close(self) -> None:
        self._client.close()

    def fetch(
        self, url: NormalizedUrl, *, accept: str = "text/html,application/xhtml+xml", cookies: Mapping[str, str] | None = None
    ) -> FetchResponse:
        """`cookies` — настройки магазина (например, город); уходят только на сайт этого магазина."""
        deadline = self._clock() + self._total_timeout_s
        cookie_header = _cookie_header(cookies or {})
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            same_store = current.store_host == url.store_host or current.store_host.endswith("." + url.store_host)
            response = self._fetch_once(current, accept, cookie_header if same_store else None, deadline)
            location = response.headers.get("location") if response.status_code in REDIRECT_STATUSES else None
            if location is None:
                body = self._read_limited(response, deadline)
                return FetchResponse(response.status_code, current, response.headers.get("content-type", ""), body)
            response.close()
            # Каждое перенаправление — новый адрес со всеми проверками, включая разрешение имени.
            current = normalize_url(urljoin(current.url, location))
        raise PriceWatcherError(ErrorCode.BLOCKED, {"reason": "too_many_redirects"})

    def _fetch_once(self, url: NormalizedUrl, accept: str, cookie_header: str | None, deadline: float) -> httpx2.Response:
        address = self._pinned_address(url.host)
        target = httpx2.URL(url.url).copy_with(host=str(address))
        headers = {"Host": url.host, "Accept": accept, "Accept-Language": "ru-RU,ru;q=0.9", "User-Agent": USER_AGENT}
        if cookie_header:
            headers["Cookie"] = cookie_header
        request = self._client.build_request(
            "GET",
            target,
            headers=headers,
            # Сертификат проверяется по имени магазина, хотя соединение идёт на адрес.
            extensions={"sni_hostname": url.host},
        )
        if self._clock() > deadline:
            raise PriceWatcherError(ErrorCode.TIMEOUT)
        try:
            response = self._client.send(request, stream=True)
        except httpx2.TimeoutException as error:
            raise PriceWatcherError(ErrorCode.TIMEOUT) from error
        except httpx2.TransportError as error:
            raise PriceWatcherError(ErrorCode.TIMEOUT, {"reason": "connection_failed"}) from error
        except httpx2.DecodingError as error:
            raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "bad_encoding"}) from error
        # Медленный ответ без тела тоже упирается в общий предел времени.
        if self._clock() > deadline:
            response.close()
            raise PriceWatcherError(ErrorCode.TIMEOUT)
        return response

    def _pinned_address(self, host: str) -> IpAddress:
        try:
            addresses = list(self._resolver(host))
        except OSError as error:
            raise PriceWatcherError(ErrorCode.TIMEOUT, {"reason": "dns_failed"}) from error
        # Все адреса имени должны быть публичными: иначе соединение могло бы уйти на любой из них.
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise PriceWatcherError(ErrorCode.INVALID_URL)
        return addresses[0]

    def _read_limited(self, response: httpx2.Response, deadline: float) -> bytes:
        chunks = []
        size = 0
        try:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self._max_bytes:
                    raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "response_too_large"})
                if self._clock() > deadline:
                    raise PriceWatcherError(ErrorCode.TIMEOUT)
                chunks.append(chunk)
        except httpx2.TimeoutException as error:
            raise PriceWatcherError(ErrorCode.TIMEOUT) from error
        except httpx2.TransportError as error:
            raise PriceWatcherError(ErrorCode.TIMEOUT, {"reason": "connection_failed"}) from error
        except httpx2.DecodingError as error:
            raise PriceWatcherError(ErrorCode.LAYOUT_CHANGED, {"reason": "bad_encoding"}) from error
        finally:
            response.close()
        return b"".join(chunks)
