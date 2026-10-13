#!/bin/sh
# Приём резервных копий на арендном сервере (DEVELOPMENT_ARCHITECTURE.md, раздел 11), запуск от root:
#   sh backup-target.sh "ssh-ed25519 AAAA… pricewatch-backup"   (открытый ключ службы копий домашнего узла)
# Пользователь pwbackup без пароля. Его ключ может только дописывать новые файлы в /srv/pricewatch-backups:
#   rrsync -wo — только запись, прочитать копии нельзя; -no-del — без удаления; -no-overwrite — лежащие не перезаписываются;
#   restrict — без терминала, пересылки портов и агента; другие команды, кроме rsync, rrsync отклоняет.
# Ключа расшифровки копий здесь нет. Копии старше 30 суток сервер удаляет сам (cron.daily), незаконченные загрузки — через сутки.
# Повторный запуск безопасен: ключ заменяется, пользователь и папка не пересоздаются.
set -eu

KEY=${1:-}
case "$KEY" in
    "ssh-ed25519 "*) ;;
    *) echo 'Нужен открытый ключ ed25519: sh backup-target.sh "ssh-ed25519 AAAA… pricewatch-backup"' >&2; exit 2 ;;
esac
case "$KEY" in
    *"
"*) echo "Ключ — одной строкой." >&2; exit 2 ;;
esac
if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите от root (sudo)." >&2
    exit 2
fi
if ! command -v rrsync >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y rsync python3
fi
RRSYNC=$(command -v rrsync)
DIR=/srv/pricewatch-backups
HOME_DIR=/var/lib/pwbackup

if ! id pwbackup >/dev/null 2>&1; then
    useradd --system --home-dir "$HOME_DIR" --create-home --shell /bin/sh pwbackup
fi
# «*» — входа по паролю нет, но учётная запись не заблокирована: sshd пускает такую по ключу.
usermod -p '*' pwbackup
install -d -o pwbackup -g pwbackup -m 700 "$DIR" "$HOME_DIR/.ssh"
umask 077
printf 'restrict,command="%s -wo -no-del -no-overwrite %s" %s\n' "$RRSYNC" "$DIR" "$KEY" > "$HOME_DIR/.ssh/authorized_keys"
chown pwbackup:pwbackup "$HOME_DIR/.ssh/authorized_keys"

umask 022
cat > /etc/cron.daily/pricewatch-backups <<'CRON'
#!/bin/sh
# Копии Price Watcher старше 30 суток и незаконченные загрузки старше суток (deploy/vps/backup-target.sh).
find /srv/pricewatch-backups -maxdepth 1 -type f -name 'pricewatch-*Z.tar.age' -mtime +30 -delete
find /srv/pricewatch-backups -maxdepth 1 -type f -name '.pricewatch-*' -mtime +1 -delete
CRON
chmod 755 /etc/cron.daily/pricewatch-backups

echo "Готово: pwbackup пишет только новые файлы в $DIR; копии старше 30 суток удаляются ежедневно."
