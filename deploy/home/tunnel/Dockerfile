# Клиент туннеля WireGuard домашнего узла (DEVELOPMENT_ARCHITECTURE.md, раздел 1): сам подключается к арендному
# серверу по имени tunnel.<домен> и при обрыве заново узнаёт его адрес. Сборка: из папки deploy/home.
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8
RUN apk add --no-cache wireguard-tools iproute2
COPY tunnel/entrypoint.sh /usr/local/bin/tunnel
ENTRYPOINT ["/usr/local/bin/tunnel"]
