# Образы Price Watcher (DEVELOPMENT_ARCHITECTURE.md, раздел 1): сборка из корня репозитория.
#   app    — сайт и API, планировщик, миграции, служебные команды; amd64 и arm64;
#   backup — то же плюс pg_dump 18 (как сервер базы) и age: планировщик сервера с ежедневной копией (scheduler --backups).
# Базовые образы закреплены по отпечатку; зависимости — точно по uv.lock, без инструментов разработки.

FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS app
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# Сначала только зависимости: слой не пересобирается, пока не меняется uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY alembic.ini ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable \
    && groupadd --system --gid 10001 pricewatch \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/pricewatch --shell /usr/sbin/nologin pricewatch \
    && mkdir -p /data/media /backups \
    && chown pricewatch:pricewatch /data/media /backups
USER pricewatch
EXPOSE 8000
CMD ["python", "-m", "pricewatch.bootstrap", "serve"]

FROM app AS backup
USER root
# pg_dump той же версии, что сервер базы, — из репозитория PostgreSQL: ключ проверяется по опубликованному отпечатку
# (B97B…4CF8, PostgreSQL Debian Repository). age — из Debian: шифрует копию открытым ключом владельца.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg age \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /tmp/pgdg.asc \
    && gpg --show-keys --with-colons /tmp/pgdg.asc | grep -q "^fpr:::::::::B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8:" \
    && gpg --dearmor -o /usr/share/keyrings/pgdg.gpg /tmp/pgdg.asc \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/pgdg.asc
USER pricewatch
CMD ["python", "-m", "pricewatch.bootstrap", "scheduler", "--backups"]
