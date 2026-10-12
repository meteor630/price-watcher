#!/bin/sh
# Поднять wg0 по /etc/wireguard/wg0.conf и держать его: если рукопожатия нет дольше 135 с (больше двух интервалов
# WireGuard), заново задать точку подключения по имени — так подхватывается новый IP арендного сервера.
set -eu

CONF=/etc/wireguard/wg0.conf
if [ ! -r "$CONF" ]; then
    echo "Нет настроек туннеля $CONF: скопируйте deploy/home/tunnel.conf.example в deploy/home/tunnel.conf и заполните." >&2
    exit 2
fi
ENDPOINT=$(awk -F' *= *' '$1 == "Endpoint" { print $2 }' "$CONF")

wg-quick up wg0
trap 'wg-quick down wg0; exit 0' TERM INT
PEER=$(wg show wg0 peers | head -n 1)
echo "Туннель поднят, сервер $ENDPOINT"

while :; do
    sleep 30 &
    wait $!
    LAST=$(wg show wg0 latest-handshakes | awk '{ print $2 }')
    if [ $(( $(date +%s) - LAST )) -gt 135 ]; then
        if wg set wg0 peer "$PEER" endpoint "$ENDPOINT"; then
            echo "Нет связи с сервером дольше 135 с: адрес $ENDPOINT разрешён заново"
        else
            echo "Нет связи с сервером, имя $ENDPOINT пока не разрешается" >&2
        fi
    fi
done
