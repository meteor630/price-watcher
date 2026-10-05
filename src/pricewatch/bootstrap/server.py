"""Запуск сервера: настройки → журнал → проверка базы → приложение → uvicorn.

Команда: uv run python -m pricewatch.bootstrap (или … serve); остальные команды — bootstrap/commands.py.
Код выхода: 2 — неверная конфигурация, 3 — база недоступна или схема не совпадает с кодом.
Uvicorn не настраивает журнал сам (log_config=None): его записи идут в общий JSON-журнал.
Свой журнал доступа uvicorn выключен — запросы пишет EdgeMiddleware, без строки запроса.
"""

import logging
from pathlib import Path

import uvicorn

from pricewatch.api.app import create_app
from pricewatch.config.settings import ConfigError, Settings, load_settings
from pricewatch.domain.errors import PriceWatcherError
from pricewatch.observability.json_log import configure_logging
from pricewatch.persistence.database import create_db_engine, server_version
from pricewatch.persistence.schema import SchemaMismatch, check_schema

logger = logging.getLogger(__name__)

EXIT_CONFIG = 2
EXIT_DATABASE = 3


def check_database(settings: Settings) -> bool:
    """База доступна и схема совпадает с кодом; иначе причина в журнале."""
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        version = server_version(engine)
        revision = check_schema(engine)
    except PriceWatcherError as error:
        logger.error("База данных недоступна", exc_info=error)
        return False
    except SchemaMismatch as error:
        logger.error(str(error))
        return False
    finally:
        engine.dispose()
    logger.info("База данных готова", extra={"server_version": version, "schema_revision": revision})
    return True


def serve(env_file: Path = Path(".env")) -> int:
    try:
        settings = load_settings(env_file)
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging(settings.log_level)
    if not check_database(settings):
        return EXIT_DATABASE
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )
    return 0
