"""Настройки туннеля WireGuard на обоих концах: дом (deploy/home/tunnel.conf.example) и арендный сервер (deploy/vps/setup.sh).

Путь между сервером и домом пропускает пакеты не больше ~1450 байт; с MTU WireGuard по умолчанию (1420 + 60 байт
заголовков) полноразмерные пакеты терялись, и TLS с сертификатом не проходил. MTU задаётся явно и одинаково.
"""

import configparser
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TUNNEL_MTU = "1280"


def interface_settings(text: str) -> dict[str, str]:
    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    return dict(parser["Interface"])


def setup_script_config() -> str:
    script = (ROOT / "deploy/vps/setup.sh").read_text(encoding="utf-8")
    match = re.search(r"cat > /etc/wireguard/wg0\.conf <<CONF\n(.*?)\nCONF\n", script, re.DOTALL)
    assert match is not None, "setup.sh не пишет /etc/wireguard/wg0.conf"
    return match.group(1)


def test_home_tunnel_sets_mtu() -> None:
    text = (ROOT / "deploy/home/tunnel.conf.example").read_text(encoding="utf-8")
    assert interface_settings(text).get("MTU") == TUNNEL_MTU


def test_server_tunnel_written_by_setup_sets_mtu() -> None:
    assert interface_settings(setup_script_config()).get("MTU") == TUNNEL_MTU


def test_server_tunnel_example_matches_setup() -> None:
    text = (ROOT / "deploy/vps/wg0.conf.example").read_text(encoding="utf-8")
    assert interface_settings(text).get("MTU") == TUNNEL_MTU


def test_interface_settings_without_mtu_are_seen() -> None:
    assert "MTU" not in interface_settings("[Interface]\nAddress = 10.66.0.2/24\n\n[Peer]\nAllowedIPs = 10.66.0.1/32\n")
