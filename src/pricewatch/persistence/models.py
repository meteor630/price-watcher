"""Таблицы PostgreSQL: пользователи, вход (этап 3), личный список покупок (этап 2), сбор цен, очередь и уведомления (этап 6).

Схема — DEVELOPMENT_ARCHITECTURE.md, раздел 5. Ограничения стоят в базе, а не только
в коде: составные внешние ключи не дают положить группу в чужую категорию или товар
в группу другой категории; частичные уникальные индексы запрещают повтор имени
среди неудалённых записей. Имена ограничений задаются правилом ниже — по ним
репозитории узнают, какое ограничение сработало.
"""

from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects.postgresql import INET, JSONB
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
    # Устаревшая цена снова включена в итог отметкой «в итоге» (с предупреждением, раздел 6).
    include_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Жизненный цикл (раздел 6): очередь проверяет товары в списке и купленные меньше 14 суток назад.
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Покупка: цена за штуку, количество, магазин (может быть не указан) и лучшая цена в момент добавления товара.
    purchase_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    purchase_quantity: Mapped[int | None] = mapped_column(Integer)
    purchase_store_id: Mapped[int | None] = mapped_column(BigInteger)
    purchase_baseline_minor: Mapped[int | None] = mapped_column(BigInteger)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        CheckConstraint("state IN ('active', 'purchased', 'archived')", name="state"),
        CheckConstraint("(state = 'purchased') = (purchased_at IS NOT NULL)", name="purchased_at_with_state"),
        CheckConstraint(
            "(state = 'purchased') = (purchase_price_minor IS NOT NULL AND purchase_quantity IS NOT NULL)", name="purchase_with_state"
        ),
        CheckConstraint(
            "state = 'purchased' OR (purchase_price_minor IS NULL AND purchase_quantity IS NULL "
            "AND purchase_store_id IS NULL AND purchase_baseline_minor IS NULL)",
            name="purchase_only_when_purchased",
        ),
        CheckConstraint("purchase_price_minor BETWEEN 1 AND 1000000000000", name="purchase_price"),
        CheckConstraint("purchase_quantity BETWEEN 1 AND 999", name="purchase_quantity"),
        CheckConstraint("purchase_baseline_minor > 0", name="purchase_baseline"),
        CheckConstraint("(state = 'archived') = (archived_at IS NOT NULL)", name="archived_at_with_state"),
        ForeignKeyConstraint(["purchase_store_id"], ["stores.id"]),
        # Цель составного ключа привязок ссылок: товар вместе с владельцем.
        UniqueConstraint("id", "user_id"),
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


# --- Сбор цен и очередь (этап 6, DEVELOPMENT_ARCHITECTURE.md, разделы 5 и 8) ---

STORE_HOST_INDEX = "uq_stores_canonical_host"
LINK_URL_INDEX = "uq_tracked_links_normalized_url_region_code"
ACTIVE_ITEM_STORE_INDEX = "uq_item_links_item_id_store_id_active"
ACTIVE_RUN_INDEX = "uq_parse_runs_one_active"
RUN_SLOT_INDEX = "uq_parse_runs_slot_key"
ACTIVE_TASK_INDEX = "uq_check_tasks_tracked_link_id_active"
TASK_OBSERVATION_INDEX = "uq_price_observations_task_id"
FETCH_STATUSES = (
    "'ok', 'INVALID_URL', 'STORE_NOT_ALLOWED', 'BLOCKED', 'CAPTCHA', 'TIMEOUT', 'LAYOUT_CHANGED', "
    "'PRICE_NOT_FOUND', 'REGION_MISMATCH', 'UNSUPPORTED_CURRENCY', 'INTERNAL_ERROR'"
)


class StoreRow(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    canonical_host: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str] = mapped_column(Text, nullable=False)
    has_card_price: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Ключ адаптера (`wildberries`, `ozon`…) или `universal` — общий парсер.
    parser_key: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    auto_created: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fetch_method: Mapped[str | None] = mapped_column(Text)
    min_delay_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    pause_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("canonical_host ~ '^[a-z0-9.-]{1,253}$'", name="canonical_host_format"),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 100", name="display_name_length"),
        CheckConstraint("color ~ '^store-[1-8]$'", name="color"),
        CheckConstraint("parser_key ~ '^[a-z_]{1,40}$'", name="parser_key_format"),
        CheckConstraint("fetch_method IS NULL OR fetch_method IN ('http', 'browser')", name="fetch_method"),
        CheckConstraint("min_delay_ms BETWEEN 0 AND 600000", name="min_delay_ms"),
        CheckConstraint("pause_level BETWEEN 0 AND 10", name="pause_level"),
        CheckConstraint("pause_reason IS NULL OR pause_reason IN ('BLOCKED', 'CAPTCHA')", name="pause_reason"),
        CheckConstraint("(paused_until IS NULL) = (pause_reason IS NULL)", name="pause_together"),
        Index(STORE_HOST_INDEX, "canonical_host", unique=True),
    )


class TrackedLinkRow(Base):
    """Ссылка из общего каталога: одна страница товара проверяется один раз, сколько бы ни было подписчиков."""

    __tablename__ = "tracked_links"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    store_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    region_code: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_title: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(["store_id"], ["stores.id"]),
        CheckConstraint("normalized_url LIKE 'https://%' AND char_length(normalized_url) <= 2048", name="normalized_url"),
        CheckConstraint("region_code ~ '^[a-z]{2,10}$'", name="region_code"),
        CheckConstraint("baseline_title IS NULL OR char_length(baseline_title) BETWEEN 1 AND 500", name="baseline_title_length"),
        Index(LINK_URL_INDEX, "normalized_url", "region_code", unique=True),
        Index("ix_tracked_links_store_id", "store_id"),
    )


class ItemLinkRow(Base):
    """Привязка ссылки к товару на отрезок [valid_from, valid_to): замена и отключение закрывают её, история цены остаётся."""

    __tablename__ = "item_links"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tracked_link_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    store_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Привязка — только к товару того же владельца.
        ForeignKeyConstraint(["item_id", "user_id"], ["items.id", "items.user_id"]),
        ForeignKeyConstraint(["tracked_link_id"], ["tracked_links.id"]),
        ForeignKeyConstraint(["store_id"], ["stores.id"]),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_range"),
        # У товара одна действующая ссылка на магазин.
        Index(ACTIVE_ITEM_STORE_INDEX, "item_id", "store_id", unique=True, postgresql_where=text("valid_to IS NULL")),
        Index("ix_item_links_tracked_link_id", "tracked_link_id"),
    )


class ParseRunRow(Base):
    __tablename__ = "parse_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Плановый слот, например 2026-10-05T09:00: один плановый запуск на слот даже при двух планировщиках.
    slot_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[int | None] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    done: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["requested_by"], ["users.id"]),
        CheckConstraint("kind IN ('scheduled', 'manual', 'on_add')", name="kind"),
        CheckConstraint("status IN ('queued', 'running', 'stopping', 'finished', 'failed')", name="status"),
        CheckConstraint("(kind = 'scheduled') = (slot_key IS NOT NULL)", name="slot_key_with_kind"),
        CheckConstraint("kind <> 'manual' OR requested_by IS NOT NULL", name="manual_requested_by"),
        CheckConstraint("total >= 0 AND done >= 0 AND failed >= 0 AND done + failed <= total", name="counters"),
        CheckConstraint("(status IN ('finished', 'failed')) = (finished_at IS NOT NULL)", name="finished_at_with_status"),
        # Не больше одного активного планового или ручного запуска (раздел 8).
        Index(
            ACTIVE_RUN_INDEX,
            text("(true)"),
            unique=True,
            postgresql_where=text("kind IN ('scheduled', 'manual') AND status IN ('queued', 'running', 'stopping')"),
        ),
        Index(RUN_SLOT_INDEX, "slot_key", unique=True, postgresql_where=text("slot_key IS NOT NULL")),
    )


class WorkerRow(Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Новый токен при ротации: оба действуют, пока worker не перейдёт на новый.
    next_token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created_at()
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _hash_check("token_hash"),
        CheckConstraint(f"next_token_hash IS NULL OR next_token_hash ~ {HASH_PATTERN}", name="next_token_hash_format"),
        CheckConstraint("next_token_hash IS NULL OR next_token_hash <> token_hash", name="tokens_differ"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 80", name="name_length"),
    )


class CheckTaskRow(Base):
    """Задание на одну ссылку; worker берёт его в аренду (FOR UPDATE SKIP LOCKED) и продлевает heartbeat."""

    __tablename__ = "check_tasks"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tracked_link_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["parse_runs.id"]),
        ForeignKeyConstraint(["tracked_link_id"], ["tracked_links.id"]),
        ForeignKeyConstraint(["worker_id"], ["workers.id"]),
        CheckConstraint("status IN ('queued', 'leased', 'done', 'failed', 'cancelled')", name="status"),
        CheckConstraint("attempt BETWEEN 0 AND 3", name="attempt"),
        CheckConstraint("(status = 'leased') = (lease_until IS NOT NULL AND worker_id IS NOT NULL)", name="lease_with_status"),
        CheckConstraint("(status IN ('done', 'failed', 'cancelled')) = (finished_at IS NOT NULL)", name="finished_at_with_status"),
        CheckConstraint(f"error_code IS NULL OR error_code IN ({FETCH_STATUSES})", name="error_code"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_ms"),
        # Не больше одного активного задания на ссылку (раздел 8).
        Index(ACTIVE_TASK_INDEX, "tracked_link_id", unique=True, postgresql_where=text("status IN ('queued', 'leased')")),
        Index("ix_check_tasks_status_priority_id", "status", "priority", "id"),
        Index("ix_check_tasks_run_id", "run_id"),
    )


class PriceObservationRow(Base):
    """Результат одной проверки ссылки. История только дополняется; повтор результата не создаёт вторую строку."""

    __tablename__ = "price_observations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tracked_link_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    price_minor: Mapped[int | None] = mapped_column(BigInteger)
    card_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    availability: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    region_code: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(["tracked_link_id"], ["tracked_links.id"]),
        ForeignKeyConstraint(["task_id"], ["check_tasks.id"]),
        CheckConstraint(f"status IN ({FETCH_STATUSES})", name="status"),
        CheckConstraint("price_minor IS NULL OR price_minor > 0", name="price_minor"),
        CheckConstraint("card_price_minor IS NULL OR card_price_minor > 0", name="card_price_minor"),
        CheckConstraint("availability IN ('in_stock', 'out_of_stock', 'unknown')", name="availability"),
        CheckConstraint("confirmation IN ('confirmed', 'pending', 'rejected')", name="confirmation"),
        CheckConstraint("source IS NULL OR source IN ('site_data', 'page')", name="source"),
        CheckConstraint("title IS NULL OR char_length(title) BETWEEN 1 AND 500", name="title_length"),
        # Сбой — без цен и без решения; успех «в наличии» — с обычной ценой (как в domain.pricing.Observation).
        CheckConstraint(
            "status = 'ok' OR (price_minor IS NULL AND card_price_minor IS NULL AND confirmation = 'confirmed' AND source IS NULL)",
            name="failure_without_price",
        ),
        CheckConstraint("status <> 'ok' OR availability = 'out_of_stock' OR price_minor IS NOT NULL", name="success_with_price"),
        Index(TASK_OBSERVATION_INDEX, "task_id", unique=True),
        Index("ix_price_observations_tracked_link_id_checked_at", "tracked_link_id", "checked_at"),
    )


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(["updated_by"], ["users.id"]),
        CheckConstraint("key ~ '^[a-z_]{1,60}$'", name="key_format"),
        CheckConstraint("version >= 1", name="version"),
    )


class PriceDecisionRow(Base):
    """Решение пользователя по подозрительному результату проверки — только для его товаров (решение владельца 07.10.2026).

    Одно решение на пару «результат — пользователь»; «Отменить» удаляет строку.
    """

    __tablename__ = "price_decisions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    observation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(["observation_id"], ["price_observations.id"]),
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        CheckConstraint("decision IN ('accept', 'reject')", name="decision"),
        UniqueConstraint("observation_id", "user_id"),
        Index("ix_price_decisions_user_id", "user_id"),
    )


class AuditEventRow(Base):
    """Журнал действий: кто (None — система), что сделал, с какой записью, до и после."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    before_json: Mapped[Any | None] = mapped_column(JSONB)
    after_json: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(["actor_id"], ["users.id"]),
        CheckConstraint("action ~ '^[a-z_]{1,60}$'", name="action_format"),
        CheckConstraint("entity_type ~ '^[a-z_]{1,60}$'", name="entity_type_format"),
        Index("ix_audit_events_actor_id_action_created_at", "actor_id", "action", "created_at"),
    )


class ClientErrorReportRow(Base):
    """Отчёт о неожиданной ошибке приложения iPhone/iPad. Недоверенный ввод: текст очищен от токенов и
    параметров адресов, нигде не выводится как HTML. Хранится 90 суток."""

    __tablename__ = "client_error_reports"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    error_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    app_version: Mapped[str] = mapped_column(Text, nullable=False)
    os_version: Mapped[str] = mapped_column(Text, nullable=False)
    screen: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        ForeignKeyConstraint(["device_id"], ["devices.id"]),
        CheckConstraint("char_length(app_version) <= 40", name="app_version_length"),
        CheckConstraint("char_length(os_version) <= 40", name="os_version_length"),
        CheckConstraint("char_length(screen) <= 80", name="screen_length"),
        CheckConstraint("char_length(error_code) <= 80", name="error_code_length"),
        CheckConstraint("char_length(message) <= 2000", name="message_length"),
        Index("ix_client_error_reports_device_id_received_at", "device_id", "received_at"),
        Index("ix_client_error_reports_received_at", "received_at"),
    )


class NotificationRow(Base):
    """Уведомление одному получателю в одном канале; одинаковые склеиваются по dedup_key.

    Telegram-строки забирает бот (sent_at, attempts), строки колокольчика читает администратор (read_at).
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Сколько раз повторился инцидент: повтор поднимает счётчик и снова делает уведомление непрочитанным.
    repeat_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        CheckConstraint("channel IN ('telegram', 'in_app')", name="channel"),
        CheckConstraint("repeat_count >= 1", name="repeat_count"),
        CheckConstraint("kind ~ '^[a-z_]{1,60}$'", name="kind_format"),
        CheckConstraint("char_length(dedup_key) BETWEEN 1 AND 200", name="dedup_key_length"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("sent_at IS NULL OR channel = 'telegram'", name="sent_only_telegram"),
        CheckConstraint("read_at IS NULL OR channel = 'in_app'", name="read_only_in_app"),
        UniqueConstraint("user_id", "channel", "dedup_key"),
    )


class IdempotencyKeyRow(Base):
    """Ключ повтора изменяющего запроса приложения: тот же ключ и тело — тот же ответ, без второго изменения.

    Строка вставляется до выполнения запроса (ответа ещё нет) и держит ключ, пока запрос идёт.
    """

    __tablename__ = "idempotency_keys"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        CheckConstraint("char_length(key) BETWEEN 8 AND 64", name="key_length"),
        CheckConstraint(f"request_hash ~ {HASH_PATTERN}", name="request_hash_format"),
        CheckConstraint("response_status IS NULL OR response_status BETWEEN 200 AND 299", name="response_status"),
        CheckConstraint("(response_status IS NULL) = (response_body IS NULL)", name="response_together"),
        Index("ix_idempotency_keys_created_at", "created_at"),
    )
