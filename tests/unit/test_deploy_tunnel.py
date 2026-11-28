"""Настройки туннеля WireGuard домашнего узла (deploy/home/tunnel.conf.example).

Путь между сервером и домом пропускает пакеты не больше ~1450 байт; с MTU WireGuard по умолчанию (1420 + 60 байт
заголовков) полноразмерные пакеты терялись, и TLS с сертификатом не проходил. MTU задаётся явно и одинаково.
"""

import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TUNNEL_MTU = "1280"


def interface_settings(text: str) -> dict[str, str]:
    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    return dict(parser["Interface"])


def test_home_tunnel_sets_mtu() -> None:
    text = (ROOT / "deploy/home/tunnel.conf.example").read_text(encoding="utf-8")
    assert interface_settings(text).get("MTU") == TUNNEL_MTU


def test_interface_settings_without_mtu_are_seen() -> None:
    assert "MTU" not in interface_settings("[Interface]\nAddress = 10.66.0.2/24\n\n[Peer]\nAllowedIPs = 10.66.0.1/32\n")
