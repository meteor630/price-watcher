"""Приглашения на PostgreSQL. Правила — в pricewatch.application.ports.InviteRepository."""

from datetime import datetime

from collections.abc import Sequence

from sqlalchemy import Connection, Row, Table, delete, select, update

from pricewatch.application.ports import InviteOverview
from pricewatch.domain.accounts import Invite, Role
from pricewatch.persistence.models import InviteRow, UserRow

invites: Table = InviteRow.__table__
users: Table = UserRow.__table__


def _to_invite(row: Row) -> Invite:
    return Invite(id=row.id, role=Role(row.role), created_by=row.created_by, expires_at=row.expires_at)


class SqlInviteRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create(self, code_hash: str, role: Role, created_by: int, created_at: datetime, expires_at: datetime) -> Invite:
        row = self.connection.execute(
            invites.insert()
            .values(
                code_hash=code_hash, role=role.value, created_by=created_by, created_at=created_at, expires_at=expires_at
            )
            .returning(invites)
        ).one()
        return _to_invite(row)

    def claim(self, code_hash: str, now: datetime) -> Invite | None:
        row = self.connection.execute(
            select(invites)
            .where(invites.c.code_hash == code_hash, invites.c.used_at.is_(None), invites.c.expires_at > now)
            .with_for_update()
        ).one_or_none()
        return None if row is None else _to_invite(row)

    def mark_used(self, invite_id: int, user_id: int, now: datetime) -> None:
        self.connection.execute(
            update(invites).where(invites.c.id == invite_id, invites.c.used_at.is_(None)).values(used_by=user_id, used_at=now)
        )

    def unused(self) -> list[InviteOverview]:
        rows = self.connection.execute(
            select(invites, users.c.display_name.label("creator"))
            .join(users, users.c.id == invites.c.created_by)
            .where(invites.c.used_at.is_(None))
            .order_by(invites.c.created_at.desc(), invites.c.id.desc())
        )
        return [InviteOverview(row.id, Role(row.role), row.creator, row.created_at, row.expires_at) for row in rows]

    def delete_unused(self, invite_ids: Sequence[int]) -> list[int]:
        deleted = self.connection.execute(
            delete(invites).where(invites.c.id.in_(list(invite_ids)), invites.c.used_at.is_(None)).returning(invites.c.id)
        )
        return sorted(row.id for row in deleted)
