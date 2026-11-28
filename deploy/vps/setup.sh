#!/bin/sh
# Арендный сервер Price Watcher с нуля (Ubuntu 24.04 и новее, Aeza или любой VPS). Запуск от root:
#   sh setup.sh <IP администратора> [ветка]
# Повторный запуск безопасен: секреты не пересоздаются, правила брандмауэра не дублируются.
#   1. Обновления системы и автоматические обновления безопасности.
#   2. Пользователь meteor (ключ — из /root/.ssh/authorized_keys, sudo), SSH только по ключу, root по SSH выключен.
#   3. Брандмауэр: входящие 443/tcp всем, 22/tcp только с IP администратора.
#   4. Docker из репозитория Ubuntu, еженедельная чистка неиспользуемых образов.
#   5. Код — /opt/price-watcher (публичный репозиторий, клонирование только на чтение).
#   6. Секреты — /srv/secrets/pricewatch (700/600): пароль базы и ключ сервера создаются здесь, в вывод не попадают.
#   7. Запуск: deploy/vps/up.sh.
# Перенос данных с домашнего узла — deploy/vps/RUNBOOK.md.
set -eu

ADMIN_IP=${1:-}
BRANCH=${2:-main}
REPO=https://github.com/meteor630/price-watcher.git
CODE=/opt/price-watcher
SECRETS=/srv/secrets/pricewatch
ADMIN=meteor

if [ -z "$ADMIN_IP" ]; then
    echo "Нужен IP администратора для SSH: sh setup.sh <IP> [ветка]" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите от root (sudo)." >&2
    exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confold full-upgrade
apt-get -y -q install unattended-upgrades ufw git docker.io docker-compose-v2 openssl
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades

# Администратор: тот же ключ, что у root при создании сервера.
if ! id "$ADMIN" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -G sudo "$ADMIN"
fi
if [ ! -s "/home/$ADMIN/.ssh/authorized_keys" ]; then
    install -d -m 700 -o "$ADMIN" -g "$ADMIN" "/home/$ADMIN/.ssh"
    install -m 600 -o "$ADMIN" -g "$ADMIN" /root/.ssh/authorized_keys "/home/$ADMIN/.ssh/authorized_keys"
fi
echo "$ADMIN ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$ADMIN
chmod 440 /etc/sudoers.d/90-$ADMIN
visudo -c >/dev/null

# 00- раньше 50-cloud-init.conf: первое значение у sshd побеждает.
printf 'PermitRootLogin no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nAllowUsers %s\n' "$ADMIN" \
    > /etc/ssh/sshd_config.d/00-hardening.conf
sshd -t
systemctl reload ssh

# SSH — первым, чтобы включение брандмауэра не оборвало текущее соединение.
ufw allow from "$ADMIN_IP" to any port 22 proto tcp
ufw allow 443/tcp
ufw default deny incoming
ufw default allow outgoing
ufw --force enable

systemctl enable --now docker
printf '#!/bin/sh\ndocker image prune -af --filter until=168h >/dev/null\ndocker builder prune -af --filter until=168h >/dev/null\n' \
    > /etc/cron.weekly/docker-prune
chmod 755 /etc/cron.weekly/docker-prune

if [ -d "$CODE/.git" ]; then
    git -C "$CODE" fetch --quiet origin "$BRANCH"
    git -C "$CODE" checkout --quiet "$BRANCH"
    git -C "$CODE" merge --quiet --ff-only "origin/$BRANCH"
else
    git clone --quiet --branch "$BRANCH" "$REPO" "$CODE"
fi
chown -R "$ADMIN:$ADMIN" "$CODE"

umask 077
install -d -m 700 "$SECRETS"
if [ ! -s "$SECRETS/compose.env" ]; then
    printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)" > "$SECRETS/compose.env"
fi
# server.env переносится с домашнего узла (тот же ключ — сессии и токены остаются рабочими); без него — новый.
if [ ! -s "$SECRETS/server.env" ]; then
    printf 'PRICEWATCH_ENV=production\nPRICEWATCH_PUBLIC_ORIGIN=https://prices.meteor630.ru\nPRICEWATCH_SECRET_KEY=%s\n' \
        "$(openssl rand -hex 48)" > "$SECRETS/server.env"
fi
chmod 600 "$SECRETS"/*

sh "$CODE/deploy/vps/up.sh"
echo "Готово. Проверка: curl -fsS https://prices.meteor630.ru/healthz"
