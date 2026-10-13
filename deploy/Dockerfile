# Образы Price Watcher (DEVELOPMENT_ARCHITECTURE.md, раздел 1): сборка из корня репозитория.
#   app    — сайт и API, планировщик, миграции, служебные команды; amd64 и arm64;
#   worker — то же плюс Google Chrome и виртуальный экран Xvfb; только amd64 (Chrome для Linux — только x86-64).
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
    && mkdir -p /data/media \
    && chown pricewatch:pricewatch /data/media
USER pricewatch
EXPOSE 8000
CMD ["python", "-m", "pricewatch.bootstrap", "serve"]

FROM app AS worker
USER root
# Google Chrome из репозитория Google: ключ проверяется по опубликованному отпечатку (EB4C…4796, Google Linux Package Signing).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg xvfb xauth fonts-liberation fonts-noto-color-emoji \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub -o /tmp/google.pub \
    && gpg --show-keys --with-colons /tmp/google.pub | grep -q "^fpr:::::::::EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796:" \
    && gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg /tmp/google.pub \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/google.pub \
    && mkdir -p /data/profiles \
    && chown pricewatch:pricewatch /data/profiles
USER pricewatch
# Chrome с окном (patchright): окно — на виртуальном экране.
CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1440x900x24", "python", "-m", "pricewatch.worker"]
