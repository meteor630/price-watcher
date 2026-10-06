"""Запуск сервера: настройки → журнал → проверка базы → приложение → uvicorn.

Команда: uv run python -m pricewatch.bootstrap (или … serve); остальные команды — bootstrap/commands.py.
Код выхода: 2 — неверная конфигурация, 3 — база недоступна или схема не совпадает с кодом.
Uvicorn не настраивает журнал сам (log_config=None): его записи идут в общий JSON-журнал.
Свой журнал доступа uvicorn выключен — запросы пишет EdgeMiddleware, без строки запроса.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from sqlalchemy import Engine

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.passwords import PasswordHasher
from pricewatch.config.settings import ConfigError, Settings, load_settings
from pricewatch.domain.errors import PriceWatcherError
from pricewatch.media.store import MediaStore
from pricewatch.observability.json_log import configure_logging
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine, server_version
from pricewatch.persistence.schema import SchemaMismatch, check_schema
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

logger = logging.getLogger(__name__)

EXIT_CONFIG = 2
EXIT_DATABASE = 3


def check_database(engine: Engine) -> bool:
    """База доступна и схема совпадает с кодом; иначе причина в журнале."""
    try:
        version = server_version(engine)
        revision = check_schema(engine)
    except PriceWatcherError as error:
        logger.error("База данных недоступна", exc_info=error)
        return False
    except SchemaMismatch as error:
        logger.error(str(error))
        return False
    logger.info("База данных готова", extra={"server_version": version, "schema_revision": revision})
    return True


def build_context(settings: Settings, engine: Engine) -> ApiContext:
    """Всё, что приложению нужно от сборки: настоящая база, серверный ключ, argon2 по умолчанию, часы UTC, каталог картинок, каталог магазинов для ссылок."""
    return ApiContext(
        unit_of_work=lambda: SqlUnitOfWork(engine),
        secret_key=settings.secret_key.get_secret_value().encode("utf-8"),
        hasher=PasswordHasher(),
        clock=lambda: datetime.now(UTC),
        images=MediaStore(settings.media_dir),
        links=LinkCatalog(),
    )


def serve(env_file: Path = Path(".env")) -> int:
    try:
        settings = load_settings(env_file)
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        if not check_database(engine):
            return EXIT_DATABASE
        uvicorn.run(
            create_app(settings, build_context(settings, engine)),
            host=settings.listen_host,
            port=settings.listen_port,
            log_config=None,
            access_log=False,
            proxy_headers=False,
            server_header=False,
        )
    finally:
        engine.dispose()
    return 0
