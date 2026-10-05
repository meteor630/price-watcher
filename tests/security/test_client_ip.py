"""IP посетителя: X-Forwarded-For учитывается только от доверенного прокси и не подделывается."""

import logging
from ipaddress import ip_address, ip_network

import pytest
from fastapi.testclient import TestClient

from pricewatch.api.app import create_app
from pricewatch.api.client_ip import client_ip
from pricewatch.config.settings import Settings

CADDY = (ip_network("10.8.0.2/32"),)
TWO_PROXIES = (ip_network("10.8.0.2/32"), ip_network("10.8.0.3/32"))


@pytest.mark.parametrize(
    ("peer", "forwarded_for", "trusted", "expected"),
    [
        # обычный путь: посетитель → Caddy → приложение
        ("10.8.0.2", ["203.0.113.7"], CADDY, "203.0.113.7"),
        # посетитель сам прислал X-Forwarded-For, Caddy дописал настоящий адрес справа
        ("10.8.0.2", ["1.1.1.1, 203.0.113.7"], CADDY, "203.0.113.7"),
        # цепочка доверенных прокси пропускается
        ("10.8.0.2", ["203.0.113.7, 10.8.0.3"], TWO_PROXIES, "203.0.113.7"),
        # несколько заголовков читаются по порядку, как один список
        ("10.8.0.2", ["1.1.1.1", "203.0.113.7"], CADDY, "203.0.113.7"),
        # все адреса доверенные — берётся самый левый
        ("10.8.0.2", ["10.8.0.3"], TWO_PROXIES, "10.8.0.3"),
        # доверенный прокси без заголовка
        ("10.8.0.2", [], CADDY, "10.8.0.2"),
        # IPv6 и IPv4, упакованный в IPv6
        ("10.8.0.2", ["2001:db8::7"], CADDY, "2001:db8::7"),
        ("::ffff:10.8.0.2", ["::ffff:203.0.113.7"], CADDY, "203.0.113.7"),
        # лишние пробелы и пустые элементы
        ("10.8.0.2", [" , 203.0.113.7 ,"], CADDY, "203.0.113.7"),
    ],
)
def test_visitor_ip_through_trusted_proxy(
    peer: str, forwarded_for: list[str], trusted: tuple, expected: str
) -> None:
    assert client_ip(peer, forwarded_for, trusted) == ip_address(expected)


@pytest.mark.parametrize(
    ("peer", "forwarded_for", "trusted"),
    [
        # соединение не от прокси — заголовок посетителя игнорируется
        ("203.0.113.7", ["1.1.1.1"], CADDY),
        # доверенных прокси нет вовсе
        ("10.8.0.2", ["1.1.1.1"], ()),
        # похожий, но не доверенный адрес
        ("10.8.0.20", ["1.1.1.1"], CADDY),
    ],
)
def test_forwarded_for_from_untrusted_peer_is_ignored(peer: str, forwarded_for: list[str], trusted: tuple) -> None:
    assert client_ip(peer, forwarded_for, trusted) == ip_address(peer)


@pytest.mark.parametrize("hops", [["garbage"], ["1.1.1.1, garbage"], ["203.0.113.7:443"], ["[2001:db8::7]"]])
def test_unreadable_rightmost_hop_falls_back_to_proxy(hops: list[str], caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pricewatch.api.client_ip"):
        result = client_ip("10.8.0.2", hops, CADDY)

    assert result == ip_address("10.8.0.2")
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_garbage_left_of_real_address_is_never_read() -> None:
    assert client_ip("10.8.0.2", ["garbage, 203.0.113.7"], CADDY) == ip_address("203.0.113.7")


@pytest.mark.parametrize("peer", [None, "", "testclient", "unix:/run/app.sock"])
def test_peer_without_ip_gives_none(peer: str | None) -> None:
    assert client_ip(peer, ["203.0.113.7"], CADDY) is None


def make_client(peer: str, trusted: str) -> TestClient:
    settings = Settings(
        env="test",
        public_origin="https://prices.example.ru",
        database_url="postgresql://pricewatch@localhost:5432/pricewatch",
        secret_key="test-secret-key-" + "k" * 32,
        trusted_proxies=trusted,
        _env_file=None,
    )  # type: ignore[call-arg]
    app = create_app(settings)

    @app.get("/t/ok")
    def ok() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app, client=(peer, 40000))


def access_log_ip(client: TestClient, caplog: pytest.LogCaptureFixture, forwarded_for: str) -> str | None:
    with caplog.at_level(logging.INFO, logger="pricewatch.api.edge"):
        client.get("/t/ok", headers={"X-Forwarded-For": forwarded_for})
    (record,) = [r for r in caplog.records if r.getMessage() == "HTTP-запрос"]
    return record.client_ip  # type: ignore[attr-defined]


def test_access_log_uses_forwarded_ip_only_from_trusted_proxy(caplog: pytest.LogCaptureFixture) -> None:
    through_caddy = make_client("10.8.0.2", "10.8.0.2/32")

    assert access_log_ip(through_caddy, caplog, "1.1.1.1, 203.0.113.7") == "203.0.113.7"


def test_access_log_ignores_spoofed_header_from_direct_connection(caplog: pytest.LogCaptureFixture) -> None:
    direct = make_client("198.51.100.9", "10.8.0.2/32")

    assert access_log_ip(direct, caplog, "203.0.113.7") == "198.51.100.9"
