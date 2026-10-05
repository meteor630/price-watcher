"""Таблицы PostgreSQL этапа 2: пользователи и личный список покупок.

Схема — DEVELOPMENT_ARCHITECTURE.md, раздел 5. Ограничения стоят в базе, а не только
в коде: составные внешние ключи не дают положить группу в чужую категорию или товар
в группу другой категории; частичные уникальные индексы запрещают повтор имени
среди неудалённых записей. Имена ограничений задаются правилом ниже — по ним
репозитории узнают, какое ограничение сработало.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}

# Индексы уникальности имён среди неудалённых записей; по ним определяется NAME_TAKEN.
CATEGORY_NAME_INDEX = "uq_categories_user_id_name_active"
GROUP_NAME_INDEX = "uq_groups_category_id_name_active"
USER_LOGIN_INDEX = "uq_users_login_lower"

ACTIVE = text("deleted_at IS NULL")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    categories_order_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created_at()
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="role"),
        CheckConstraint("char_length(login) BETWEEN 3 AND 64", name="login_length"),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 100", name="display_name_length"),
        CheckConstraint("categories_order_version >= 1", name="categories_order_version"),
        Index(USER_LOGIN_INDEX, func.lower(text("login")), unique=True),
    )


class CategoryRow(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        # Цель составных ключей групп и товаров: категория вместе с владельцем.
        UniqueConstraint("id", "user_id"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint("name_normalized <> ''", name="name_normalized"),
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint("version >= 1", name="version"),
        Index(CATEGORY_NAME_INDEX, "user_id", "name_normalized", unique=True, postgresql_where=ACTIVE),
        Index("ix_categories_user_id_position", "user_id", "position"),
    )


class GroupRow(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Группа лежит только в категории своего владельца.
        ForeignKeyConstraint(["category_id", "user_id"], ["categories.id", "categories.user_id"]),
        # Цель составного ключа товаров: группа вместе с категорией и владельцем.
        UniqueConstraint("id", "category_id", "user_id"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint("name_normalized <> ''", name="name_normalized"),
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint("version >= 1", name="version"),
        Index(GROUP_NAME_INDEX, "category_id", "name_normalized", unique=True, postgresql_where=ACTIVE),
        Index("ix_groups_category_id_position", "category_id", "position"),
    )


class ItemRow(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    group_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    in_total: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["category_id", "user_id"], ["categories.id", "categories.user_id"]),
        # Если группа указана, она из той же категории и того же владельца.
        ForeignKeyConstraint(
            ["group_id", "category_id", "user_id"], ["groups.id", "groups.category_id", "groups.user_id"]
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),
        CheckConstraint("full_name IS NULL OR char_length(full_name) BETWEEN 1 AND 500", name="full_name_length"),
        CheckConstraint("quantity BETWEEN 1 AND 999", name="quantity"),
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_items_category_id_group_id_position", "category_id", "group_id", "position"),
    )
