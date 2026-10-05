"""IP посетителя с учётом только доверенных прокси.

Caddy дописывает настоящий адрес посетителя в конец X-Forwarded-For. Поэтому
список читается справа налево: доверенные прокси пропускаются, первый
недоверенный адрес — посетитель. Всё левее него мог написать сам посетитель,
и это не читается. Если соединение пришло не от доверенного прокси, заголовок
игнорируется целиком.
"""

import logging
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address

logger = logging.getLogger(__name__)

IPAddress = IPv4Address | IPv6Address
Network = IPv4Network | IPv6Network


def client_ip(
    peer: str | None,
    forwarded_for: Iterable[str],
    trusted_proxies: tuple[Network, ...],
) -> IPAddress | None:
    """`peer` — адрес собеседника TCP; `forwarded_for` — значения заголовков X-Forwarded-For по порядку."""
    peer_ip = _parse(peer)
    if peer_ip is None or not _is_trusted(peer_ip, trusted_proxies):
        return peer_ip

    hops = [hop.strip() for header in forwarded_for for hop in header.split(",") if hop.strip()]
    candidate = peer_ip
    for hop in reversed(hops):
        hop_ip = _parse(hop)
        if hop_ip is None:
            logger.warning(
                "Доверенный прокси передал неразборчивый X-Forwarded-For; использован адрес прокси",
                extra={"proxy_ip": str(candidate)},
            )
            return candidate
        candidate = hop_ip
        if not _is_trusted(hop_ip, trusted_proxies):
            return hop_ip
    return candidate


def _parse(value: str | None) -> IPAddress | None:
    if not value:
        return None
    try:
        parsed = ip_address(value)
    except ValueError:
        return None
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def _is_trusted(address: IPAddress, trusted_proxies: tuple[Network, ...]) -> bool:
    return any(address.version == network.version and address in network for network in trusted_proxies)
