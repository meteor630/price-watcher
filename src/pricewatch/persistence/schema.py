"""Настройка Alembic из кода: без alembic.ini и без пароля в файлах."""

from alembic.config import Config

SCRIPT_LOCATION = "pricewatch.persistence:migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    # Через attributes, а не set_main_option: в пароле может быть %, а опции проходят интерполяцию.
    config.attributes["database_url"] = database_url
    return config
