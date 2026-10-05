"""Запуск сервера: настройки → журнал → приложение → uvicorn.

Команда: uv run python -m pricewatch.bootstrap
Uvicorn не настраивает журнал сам (log_config=None): его записи идут в общий JSON-журнал.
Свой журнал доступа uvicorn выключен — запросы пишет EdgeMiddleware, без строки запроса.
"""

import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from pricewatch.api.app import create_app
from pricewatch.config.settings import ConfigError, Settings, load_settings
from pricewatch.observability.json_log import configure_logging

logger = logging.getLogger(__name__)


def build_app(settings: Settings) -> FastAPI:
    configure_logging(settings.log_level)
    return create_app(settings)


def main(env_file: Path = Path(".env")) -> int:
    try:
        settings = load_settings(env_file)
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return 2
    app = build_app(settings)
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )
    return 0
