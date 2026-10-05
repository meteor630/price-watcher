"""Alembic из кода: настройка без alembic.ini и проверка, что схема базы совпадает с кодом."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from pricewatch.domain.errors import ErrorCode, PriceWatcherError

SCRIPT_LOCATION = "pricewatch.persistence:migrations"


class SchemaMismatch(Exception):
    """Ревизия схемы в базе не та, которую ожидает код. Текст — что делать, без адреса базы."""


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    if database_url is not None:
        # Через attributes, а не set_main_option: в пароле может быть %, а опции проходят интерполяцию.
        config.attributes["database_url"] = database_url
    return config


def head_revision() -> str:
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"У миграций должна быть одна последняя ревизия, найдено: {len(heads)}")
    return heads[0]


def current_revision(engine: Engine) -> str | None:
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    except SQLAlchemyError as error:
        raise PriceWatcherError(ErrorCode.DB_ERROR) from error


def check_schema(engine: Engine) -> str:
    """Ревизия схемы, если она совпадает с кодом; иначе SchemaMismatch с объяснением."""
    head = head_revision()
    current = current_revision(engine)
    if current == head:
        return head
    known = {script.revision for script in ScriptDirectory.from_config(alembic_config()).walk_revisions()}
    if current is not None and current not in known:
        raise SchemaMismatch(
            f"Схема базы в ревизии {current}, неизвестной этой версии кода (ожидается {head}): "
            "вероятно, базу обновила более новая версия. Запустите её или восстановите базу из копии."
        )
    state = f"в ревизии {current}" if current else "пустая"
    raise SchemaMismatch(f"Схема базы {state}, код ожидает {head}. Выполните: uv run alembic upgrade head")
