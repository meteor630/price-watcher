#!/bin/sh
# Настройка арендного сервера (Ubuntu 24.04 и новее), запуск от root:  sh setup.sh <открытый ключ домашнего узла>
# Повторный запуск безопасен: ключи сервера не пересоздаются, правила брандмауэра не дублируются.
#   1. WireGuard: интерфейс 10.66.0.1, порт 51820/udp, MTU 1280 (как дома), единственный собеседник — дом 10.66.0.2.
#   2. Брандмауэр: входящие только 22/tcp (SSH), 80/tcp, 443/tcp, 51820/udp.
#   3. Docker для HAProxy (и бота, срез 11.2) — только если его ещё нет: установленный из репозитория Docker
#      (docker-ce) не трогаем, пакет Ubuntu docker.io с ним конфликтует.
# В конце печатает открытый ключ сервера — его вписать в deploy/home/tunnel.conf (PublicKey).
set -eu

HOME_KEY=${1:-}
if [ -z "$HOME_KEY" ]; then
    echo "Нужен открытый ключ домашнего узла: sh setup.sh <ключ>" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите от root (sudo)." >&2
    exit 2
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard-tools ufw
if ! docker compose version >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
fi

umask 077
if [ ! -s /etc/wireguard/server.key ]; then
    wg genkey > /etc/wireguard/server.key
fi
cat > /etc/wireguard/wg0.conf <<CONF
[Interface]
Address = 10.66.0.1/24
ListenPort = 51820
MTU = 1280
PrivateKey = $(cat /etc/wireguard/server.key)

[Peer]
PublicKey = $HOME_KEY
AllowedIPs = 10.66.0.2/32
CONF
systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0

# SSH — первым, чтобы включение брандмауэра не оборвало текущее соединение.
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 51820/udp
ufw default deny incoming
ufw default allow outgoing
ufw --force enable

systemctl enable --now docker
echo
echo "Открытый ключ сервера (вписать в deploy/home/tunnel.conf → PublicKey):"
wg pubkey < /etc/wireguard/server.key
