"""Проверяющий прокси для браузера worker (DEVELOPMENT_ARCHITECTURE.md, раздел 7, «Защита от SSRF»).

Chrome ходит в интернет только через этот прокси (`--proxy-server`, петлевые адреса тоже).
Пропускается одно: `CONNECT имя:443`, где имя — не IP, а все его адреса публичные. Прокси
сам разрешает имя один раз и соединяется ровно с проверенным адресом, поэтому подмена DNS
(rebinding) не работает. Шифрование не вскрывается: после проверки байты просто передаются
в обе стороны. Через прокси идут и перенаправления, и всплывающие окна, и WebSocket — то,
что перехват запросов Playwright пропускает (проверено 06.10.2026: шаги перенаправления
мимо `route`).
"""

import logging
import selectors
import socket
import socketserver
import threading
from collections.abc import Callable

from pricewatch.domain.errors import PriceWatcherError
from pricewatch.parsers.fetch import IpAddress, Resolver, is_public_address, system_resolver
from pricewatch.parsers.urls import normalize_url

logger = logging.getLogger(__name__)

HEADER_MAX = 8192
HEADER_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 5.0
IDLE_TIMEOUT_S = 60.0
CONNECTIONS_MAX = 64
CHUNK = 65536

Opener = Callable[[tuple[str, int], float], socket.socket]


def _open(address: tuple[str, int], timeout: float) -> socket.socket:
    return socket.create_connection(address, timeout=timeout)


class EgressProxy:
    def __init__(self, *, resolver: Resolver = system_resolver, opener: Opener = _open) -> None:
        self._resolver = resolver
        self._opener = opener
        self._slots = threading.BoundedSemaphore(CONNECTIONS_MAX)
        proxy = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                proxy._handle(self.request)

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="egress-proxy", daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handle(self, client: socket.socket) -> None:
        if not self._slots.acquire(blocking=False):
            _reply(client, b"503 Service Unavailable")
            return
        try:
            target = self._read_target(client)
            if target is None:
                return
            address = self._checked_address(target)
            if address is None:
                _reply(client, b"403 Forbidden")
                return
            try:
                upstream = self._opener((str(address), 443), CONNECT_TIMEOUT_S)
            except OSError:
                _reply(client, b"502 Bad Gateway")
                return
            with upstream:
                _reply(client, b"200 Connection Established")
                _relay(client, upstream)
        finally:
            self._slots.release()

    def _read_target(self, client: socket.socket) -> str | None:
        """Имя из `CONNECT имя:443 HTTP/1.1` или None (ответ клиенту уже отправлен)."""
        client.settimeout(HEADER_TIMEOUT_S)
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = client.recv(1024)
                if not chunk:
                    return None
                data += chunk
                if len(data) > HEADER_MAX:
                    _reply(client, b"431 Request Header Fields Too Large")
                    return None
        except OSError:
            return None
        parts = data.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
        if len(parts) != 3 or parts[0] != "CONNECT":
            # Обычный http-запрос через прокси — это не https, такие не пропускаются.
            logger.info("Браузер: запрос отклонён", extra={"reason": "not_connect"})
            _reply(client, b"403 Forbidden")
            return None
        host, _, port = parts[1].rpartition(":")
        if port != "443" or not host:
            logger.info("Браузер: запрос отклонён", extra={"reason": "port", "target": parts[1][:200]})
            _reply(client, b"403 Forbidden")
            return None
        return host

    def _checked_address(self, host: str) -> IpAddress | None:
        try:
            name = normalize_url(f"https://{host}/").host
        except PriceWatcherError:
            logger.info("Браузер: запрос отклонён", extra={"reason": "not_a_name", "target": host[:200]})
            return None
        address = public_address(name, self._resolver)
        if address is None:
            logger.info("Браузер: запрос отклонён", extra={"reason": "not_public", "target": name})
        return address


def public_address(name: str, resolver: Resolver) -> IpAddress | None:
    """Первый адрес имени, если все его адреса публичные; иначе None (и при сбое разрешения)."""
    try:
        addresses = list(resolver(name))
    except OSError:
        return None
    if not addresses or not all(is_public_address(address) for address in addresses):
        return None
    return addresses[0]


def _reply(client: socket.socket, status: bytes) -> None:
    try:
        client.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    except OSError:
        logger.debug("Браузер закрыл соединение до ответа прокси")


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    """Передавать байты в обе стороны, пока одна не закроется или соединение не простаивает."""
    client.settimeout(None)
    upstream.settimeout(None)
    peers = {client: upstream, upstream: client}
    with selectors.DefaultSelector() as selector:
        for sock in peers:
            selector.register(sock, selectors.EVENT_READ)
        while True:
            events = selector.select(IDLE_TIMEOUT_S)
            if not events:
                return
            for key, _ in events:
                source = key.fileobj
                try:
                    data = source.recv(CHUNK)  # type: ignore[union-attr]
                    if not data:
                        return
                    peers[source].sendall(data)  # type: ignore[index]
                except OSError:
                    return
