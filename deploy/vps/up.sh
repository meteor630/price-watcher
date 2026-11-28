#!/bin/sh
# Сборка и запуск Price Watcher на арендном сервере с секретами из /srv/secrets/pricewatch.
set -eu
cd "$(dirname "$0")"
exec docker compose --env-file /srv/secrets/pricewatch/compose.env up -d --build --remove-orphans "$@"
