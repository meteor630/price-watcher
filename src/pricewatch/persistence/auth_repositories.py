"""Неудачные попытки входа и сессии сайта на PostgreSQL. Правила — в pricewatch.application.ports."""

from datetime import datetime

from sqlalchemy import Connection, Row, Table, cast, func, select, update
from sqlalchemy.dialects.postgresql import INET

from pricewatch.application.ports import FailureWindow
from pricewatch.domain.accounts import WebSession
from pricewatch.persistence.models import AuthFailureRow, WebSessionRow

auth_failures: Table = AuthFailureRow.__table__
web_sessions: Table = WebSessionRow.__table__


class SqlAuthFailureRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def record(self, kind: str, subject: str | None, ip: str | None, now: datetime) -> None:
        self.connection.execute(
            auth_failures.insert().values(
                kind=kind, subject=subject, ip=None if ip is None else cast(ip, INET), created_at=now
            )
        )

    def window_for_subject(self, kind: str, subject: str, since: datetime) -> FailureWindow:
        return self._window(auth_failures.c.subject == subject, kind, since)

    def window_for_ip(self, kind: str, ip: str, since: datetime) -> FailureWindow:
        return self._window(auth_failures.c.ip == cast(ip, INET), kind, since)

    def clear_subject(self, kind: str, subject: str) -> None:
        self.connection.execute(
            auth_failures.delete().where(auth_failures.c.kind == kind, auth_failures.c.subject == subject)
        )

    def _window(self, condition: object, kind: str, since: datetime) -> FailureWindow:
        count, oldest = self.connection.execute(
            select(func.count(), func.min(auth_failures.c.created_at)).where(
                condition, auth_failures.c.kind == kind, auth_failures.c.created_at > since
            )
        ).one()
        return FailureWindow(count=count, oldest=oldest)


def _to_session(row: Row) -> WebSession:
    return WebSession(
        id=row.id,
        user_id=row.user_id,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
    )


class SqlSessionRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create(self, user_id: int, token_hash: str, created_at: datetime, expires_at: datetime) -> WebSession:
        row = self.connection.execute(
            web_sessions.insert()
            .values(
                user_id=user_id,
                token_hash=token_hash,
                created_at=created_at,
                last_seen_at=created_at,
                expires_at=expires_at,
            )
            .returning(web_sessions)
        ).one()
        return _to_session(row)

    def find_active(self, token_hash: str, now: datetime) -> WebSession | None:
        row = self.connection.execute(
            select(web_sessions).where(
                web_sessions.c.token_hash == token_hash,
                web_sessions.c.revoked_at.is_(None),
                web_sessions.c.expires_at > now,
            )
        ).one_or_none()
        return None if row is None else _to_session(row)

    def touch(self, session_id: int, now: datetime) -> None:
        self.connection.execute(update(web_sessions).where(web_sessions.c.id == session_id).values(last_seen_at=now))

    def revoke(self, session_id: int, now: datetime) -> None:
        self.connection.execute(
            update(web_sessions)
            .where(web_sessions.c.id == session_id, web_sessions.c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
