"""Ключи повтора запросов на PostgreSQL. Правила — в pricewatch.application.ports (IdempotencyRepository)."""

from datetime import datetime

from sqlalchemy import Connection, Table, delete, select, update
from sqlalchemy.dialects.postgresql import insert

from pricewatch.application.ports import StoredRequest
from pricewatch.persistence.models import IdempotencyKeyRow

keys: Table = IdempotencyKeyRow.__table__


class SqlIdempotencyRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def drop_stale(self, user_id: int, key: str, expired_before: datetime, abandoned_before: datetime) -> None:
        self.connection.execute(
            delete(keys).where(
                keys.c.user_id == user_id,
                keys.c.key == key,
                (keys.c.created_at < expired_before) | (keys.c.response_status.is_(None) & (keys.c.created_at < abandoned_before)),
            )
        )

    def claim(self, user_id: int, key: str, request_hash: str, now: datetime) -> bool:
        inserted = self.connection.execute(
            insert(keys)
            .values(user_id=user_id, key=key, request_hash=request_hash, created_at=now)
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(keys.c.key)
        ).scalar_one_or_none()
        return inserted is not None

    def get(self, user_id: int, key: str) -> StoredRequest | None:
        row = self.connection.execute(
            select(keys.c.request_hash, keys.c.response_status, keys.c.response_body).where(keys.c.user_id == user_id, keys.c.key == key)
        ).one_or_none()
        return None if row is None else StoredRequest(row.request_hash, row.response_status, row.response_body)

    def complete(self, user_id: int, key: str, status: int, body: object) -> None:
        self.connection.execute(
            update(keys).where(keys.c.user_id == user_id, keys.c.key == key).values(response_status=status, response_body=body)
        )

    def release(self, user_id: int, key: str) -> None:
        self.connection.execute(delete(keys).where(keys.c.user_id == user_id, keys.c.key == key, keys.c.response_status.is_(None)))

    def purge(self, created_before: datetime) -> int:
        return self.connection.execute(delete(keys).where(keys.c.created_at < created_before)).rowcount
