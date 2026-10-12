"""Telegram: коды привязки, чат пользователя, сообщения для бота (захват строк — FOR UPDATE SKIP LOCKED)."""

from datetime import datetime

from sqlalchemy import Connection, Table, select, update

from pricewatch.application.ports import OutboxMessage
from pricewatch.domain.accounts import User
from pricewatch.persistence.models import NotificationRow, TelegramLinkCodeRow, UserRow
from pricewatch.persistence.user_repository import to_user

users: Table = UserRow.__table__
link_codes: Table = TelegramLinkCodeRow.__table__
notifications: Table = NotificationRow.__table__


class SqlTelegramRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create_link_code(self, user_id: int, code_hash: str, now: datetime, expires_at: datetime) -> None:
        self.connection.execute(link_codes.insert().values(user_id=user_id, code_hash=code_hash, created_at=now, expires_at=expires_at))

    def use_link_code(self, code_hash: str, now: datetime) -> int | None:
        return self.connection.execute(
            update(link_codes)
            .where(link_codes.c.code_hash == code_hash, link_codes.c.used_at.is_(None), link_codes.c.expires_at > now)
            .values(used_at=now)
            .returning(link_codes.c.user_id)
        ).scalar_one_or_none()

    def set_chat(self, user_id: int, chat_id: int) -> None:
        self.connection.execute(update(users).where(users.c.telegram_chat_id == chat_id, users.c.id != user_id).values(telegram_chat_id=None))
        self.connection.execute(update(users).where(users.c.id == user_id).values(telegram_chat_id=chat_id))

    def clear_chat(self, user_id: int) -> None:
        self.connection.execute(update(users).where(users.c.id == user_id).values(telegram_chat_id=None))

    def is_linked(self, user_id: int) -> bool:
        return self.connection.execute(select(users.c.telegram_chat_id).where(users.c.id == user_id)).scalar_one() is not None

    def user_by_chat(self, chat_id: int) -> User | None:
        row = self.connection.execute(select(users).where(users.c.telegram_chat_id == chat_id)).one_or_none()
        return None if row is None else to_user(row)

    def claim_outbox(self, now: datetime, claimed_until: datetime, limit: int, attempts_max: int) -> list[OutboxMessage]:
        free = (
            select(notifications.c.id)
            .join(users, users.c.id == notifications.c.user_id)
            .where(
                notifications.c.channel == "telegram",
                notifications.c.sent_at.is_(None),
                notifications.c.attempts < attempts_max,
                notifications.c.claimed_until.is_(None) | (notifications.c.claimed_until <= now),
                users.c.telegram_chat_id.is_not(None),
                users.c.disabled_at.is_(None),
            )
            .order_by(notifications.c.created_at, notifications.c.id)
            .limit(limit)
            .with_for_update(of=notifications, skip_locked=True)
        )
        ids = list(self.connection.execute(free).scalars())
        if not ids:
            return []
        self.connection.execute(update(notifications).where(notifications.c.id.in_(ids)).values(claimed_until=claimed_until))
        rows = self.connection.execute(
            select(notifications, users.c.telegram_chat_id)
            .join(users, users.c.id == notifications.c.user_id)
            .where(notifications.c.id.in_(ids))
            .order_by(notifications.c.created_at, notifications.c.id)
        )
        return [OutboxMessage(row.id, row.telegram_chat_id, row.kind, row.payload_json, row.repeat_count) for row in rows]

    def mark_sent(self, notification_id: int, now: datetime) -> bool:
        return self.connection.execute(
            update(notifications)
            .where(notifications.c.id == notification_id, notifications.c.channel == "telegram", notifications.c.sent_at.is_(None))
            .values(sent_at=now, claimed_until=None)
        ).rowcount == 1

    def mark_failed(self, notification_id: int, retry_at: datetime) -> bool:
        return self.connection.execute(
            update(notifications)
            .where(notifications.c.id == notification_id, notifications.c.channel == "telegram", notifications.c.sent_at.is_(None))
            .values(attempts=notifications.c.attempts + 1, claimed_until=retry_at)
        ).rowcount == 1
