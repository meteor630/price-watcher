"""Подключение к PostgreSQL.

Пул небольшой: на Raspberry Pi память делят база, сервер и браузер worker.
Параметры запросов не попадают в тексты ошибок (hide_parameters), пароль в адресе
SQLAlchemy при выводе маскирует сам, а настройки регистрируют его для журнала.
"""

from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import SQLAlchemyError

from pricewatch.domain.errors import ErrorCode, PriceWatcherError

POOL_SIZE = 5
MAX_OVERFLOW = 5
CONNECT_TIMEOUT_S = 5
POOL_RECYCLE_S = 1800


def create_db_engine(database_url: str) -> Engine:
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    return create_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_S,
        hide_parameters=True,
        # Время из базы всегда в UTC, как требует контракт API, независимо от настройки сервера.
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_S,
            "application_name": "pricewatch",
            "options": "-c timezone=UTC",
        },
    )


def server_version(engine: Engine) -> str:
    """Версия сервера; недоступная база даёт DB_ERROR, причина остаётся в цепочке исключений."""
    try:
        with engine.connect() as connection:
            return str(connection.execute(text("SHOW server_version")).scalar_one())
    except SQLAlchemyError as error:
        raise PriceWatcherError(ErrorCode.DB_ERROR) from error
