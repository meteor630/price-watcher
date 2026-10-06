"""pricewatch.parsers.egress: прокси пропускает только CONNECT имя:443 с публичными адресами.

Настоящие сокеты, но без интернета: «магазин» — локальный эхо-сервер, к которому прокси
подключается через подменённую функцию соединения; она же записывает, куда прокси хотел идти.
"""

import ipaddress
import socket
import socketserver
import threading
from collections.abc import Iterator

import pytest

from pricewatch.parsers.egress import HEADER_MAX, EgressProxy, public_address

PUBLIC = ipaddress.ip_address("93.184.216.34")


class Echo(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(1024):
            self.request.sendall(b"echo:" + data)


@pytest.fixture(scope="module")
def echo() -> Iterator[int]:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield int(server.server_address[1])
    server.shutdown()
    server.server_close()


RESOLVED: list[str] = []
OPENED: list[tuple[str, int]] = []
TABLE = {
    "shop.example": [PUBLIC],
    "router.example": [ipaddress.ip_address("192.168.1.1")],
    "mixed.example": [PUBLIC, ipaddress.ip_address("10.0.0.1")],
    "nat64.example": [ipaddress.ip_address("64:ff9b::a00:1")],
}


@pytest.fixture(scope="module")
def shared_proxy(echo: int) -> Iterator[EgressProxy]:
    def resolver(host: str) -> list:
        RESOLVED.append(host)
        if host == "broken.example":
            raise OSError("no such host")
        return TABLE.get(host, [])

    def opener(address: tuple[str, int], timeout: float) -> socket.socket:
        OPENED.append(address)
        return socket.create_connection(("127.0.0.1", echo), timeout=timeout)

    egress = EgressProxy(resolver=resolver, opener=opener)
    yield egress
    egress.close()


@pytest.fixture
def proxy(shared_proxy: EgressProxy) -> tuple[EgressProxy, list, list]:
    RESOLVED.clear()
    OPENED.clear()
    return shared_proxy, RESOLVED, OPENED


def ask(port: int, request: bytes) -> tuple[bytes, socket.socket]:
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.sendall(request)
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = client.recv(1)
        if not chunk:
            break
        head += chunk
    return head.split(b"\r\n", 1)[0], client


def test_connect_to_public_name_is_tunneled_to_checked_address(proxy) -> None:  # type: ignore[no-untyped-def]
    egress, resolved, opened = proxy

    status, client = ask(egress.port, b"CONNECT shop.example:443 HTTP/1.1\r\nHost: shop.example:443\r\n\r\n")
    with client:
        client.sendall(b"tls-bytes")
        assert client.recv(100) == b"echo:tls-bytes"

    assert status == b"HTTP/1.1 200 Connection Established"
    assert resolved == ["shop.example"]  # одно разрешение имени
    assert opened == [("93.184.216.34", 443)]  # соединение — ровно на проверенный адрес


def test_name_is_normalized_before_check(proxy) -> None:  # type: ignore[no-untyped-def]
    egress, resolved, _ = proxy

    status, client = ask(egress.port, b"CONNECT SHOP.Example:443 HTTP/1.1\r\n\r\n")
    client.close()

    assert status.endswith(b"200 Connection Established")
    assert resolved == ["shop.example"]


@pytest.mark.parametrize(
    "request_line",
    [
        b"CONNECT router.example:443 HTTP/1.1",  # имя ведёт в домашнюю сеть
        b"CONNECT mixed.example:443 HTTP/1.1",  # среди адресов есть внутренний
        b"CONNECT nat64.example:443 HTTP/1.1",  # NAT64 на 10.0.0.1
        b"CONNECT unknown.example:443 HTTP/1.1",  # имя не разрешилось
        b"CONNECT broken.example:443 HTTP/1.1",  # сбой разрешения
        b"CONNECT 127.0.0.1:443 HTTP/1.1",  # IP вместо имени
        b"CONNECT 192.168.1.1:443 HTTP/1.1",
        b"CONNECT [::1]:443 HTTP/1.1",
        b"CONNECT localhost:443 HTTP/1.1",
        b"CONNECT shop.example:80 HTTP/1.1",  # только 443
        b"CONNECT shop.example:8443 HTTP/1.1",
        b"CONNECT shop.example HTTP/1.1",
        b"GET http://shop.example/ HTTP/1.1",  # обычный http через прокси — не https
        b"GET http://127.0.0.1:8080/admin HTTP/1.1",
        b"POST http://192.168.1.1/reboot HTTP/1.1",
        b"garbage",
    ],
)
def test_everything_else_is_refused_without_connecting(proxy, request_line: bytes) -> None:  # type: ignore[no-untyped-def]
    egress, _, opened = proxy

    status, client = ask(egress.port, request_line + b"\r\n\r\n")
    client.close()

    assert status == b"HTTP/1.1 403 Forbidden"
    assert opened == []


def test_oversized_header_is_refused(proxy) -> None:  # type: ignore[no-untyped-def]
    egress, _, opened = proxy

    status, client = ask(egress.port, b"CONNECT shop.example:443 HTTP/1.1\r\nX: " + b"a" * HEADER_MAX + b"\r\n\r\n")
    client.close()

    assert status == b"HTTP/1.1 431 Request Header Fields Too Large"
    assert opened == []


def test_unreachable_shop_is_bad_gateway(echo: int) -> None:
    def refuse(address: tuple[str, int], timeout: float) -> socket.socket:
        raise ConnectionRefusedError

    egress = EgressProxy(resolver=lambda host: [PUBLIC], opener=refuse)
    try:
        status, client = ask(egress.port, b"CONNECT shop.example:443 HTTP/1.1\r\n\r\n")
        client.close()
    finally:
        egress.close()

    assert status == b"HTTP/1.1 502 Bad Gateway"


def test_client_hanging_up_mid_header_is_harmless(proxy) -> None:  # type: ignore[no-untyped-def]
    egress, _, opened = proxy
    with socket.create_connection(("127.0.0.1", egress.port), timeout=5) as client:
        client.sendall(b"CONNECT shop.exa")

    # Прокси продолжает работать для следующих клиентов.
    status, client = ask(egress.port, b"CONNECT shop.example:443 HTTP/1.1\r\n\r\n")
    client.close()
    assert status.endswith(b"200 Connection Established")
    assert opened == [("93.184.216.34", 443)]


def test_public_address_helper() -> None:
    assert public_address("a.example", lambda host: [PUBLIC]) == PUBLIC
    assert public_address("a.example", lambda host: []) is None
    assert public_address("a.example", lambda host: [ipaddress.ip_address("10.0.0.1")]) is None
