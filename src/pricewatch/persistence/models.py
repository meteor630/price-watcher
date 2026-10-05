"""Таблицы PostgreSQL: пользователи, вход (этап 3) и личный список покупок (этап 2).

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
from sqlalchemy.dialects.postgresql import INET
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
# Во всех *_hash — только HMAC-SHA256 в шестнадцатеричном виде: токен в открытом виде база не примет.
HASH_PATTERN = "'^[0-9a-f]{64}$'"


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


def _hash_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ {HASH_PATTERN}", name=f"{column}_format")


class InviteRow(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_by: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["created_by"], ["users.id"]),
        ForeignKeyConstraint(["used_by"], ["users.id"]),
        _hash_check("code_hash"),
        CheckConstraint("role IN ('user', 'admin')", name="role"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        CheckConstraint("(used_by IS NULL) = (used_at IS NULL)", name="used_together"),
    )


class WebSessionRow(Base):
    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        _hash_check("token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_web_sessions_user_id", "user_id"),
    )


class DeviceRow(Base):
    """Устройство приложения; оно же семья refresh-токенов."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime] = _created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="name_length"),
        Index("ix_devices_user_id", "user_id"),
    )


class RefreshTokenRow(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = _created_at()
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["device_id"], ["devices.id"]),
        _hash_check("token_hash"),
        CheckConstraint("expires_at > issued_at", name="expires_after_issued"),
        Index("ix_refresh_tokens_device_id", "device_id"),
    )


class AccessTokenRow(Base):
    __tablename__ = "access_tokens"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["device_id"], ["devices.id"]),
        _hash_check("token_hash"),
        CheckConstraint("expires_at > issued_at", name="expires_after_issued"),
        Index("ix_access_tokens_device_id", "device_id"),
    )


class PairingCodeRow(Base):
    __tablename__ = "pairing_codes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        _hash_check("code_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_pairing_codes_user_id", "user_id"),
    )


class AuthFailureRow(Base):
    """Неудачная попытка входа (login) или ввода кода связки (pairing) — для ограничений."""

    __tablename__ = "auth_failures"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("kind IN ('login', 'pairing')", name="kind"),
        CheckConstraint("subject IS NULL OR char_length(subject) BETWEEN 1 AND 64", name="subject_length"),
        Index("ix_auth_failures_kind_subject_created_at", "kind", "subject", "created_at"),
        Index("ix_auth_failures_kind_ip_created_at", "kind", "ip", "created_at"),
    )
