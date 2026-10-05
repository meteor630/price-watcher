"""Окружение Alembic: адрес базы из настроек проекта, журнал в JSON."""

from alembic import context

from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.models import Base

config = context.config


def database_url() -> str:
    # Тесты и инструменты передают адрес напрямую; команда alembic берёт его из настроек.
    url = config.attributes.get("database_url")
    if url:
        return str(url)
    from pricewatch.config.settings import load_settings

    return load_settings().database_url.get_secret_value()


def run_migrations() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Миграции выполняются только на подключённой базе: офлайн-режим не поддерживается")
    if config.config_file_name is not None:
        from pricewatch.observability.json_log import configure_logging

        configure_logging("INFO")
    engine = create_db_engine(database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
