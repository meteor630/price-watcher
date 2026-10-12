"""Пользователи на PostgreSQL. Правила — в pricewatch.application.ports.UserRepository."""

from datetime import datetime

from sqlalchemy import Connection, Row, Table, Update, func, select, update
from sqlalchemy.exc import IntegrityError

from pricewatch.application.ports import UserCredentials, UserOverview
from pricewatch.domain.accounts import Role, User, login_key
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.models import USER_LOGIN_INDEX, UserRow, WebSessionRow

users: Table = UserRow.__table__
web_sessions: Table = WebSessionRow.__table__


def to_user(row: Row) -> User:
    return User(
        id=row.id,
        login=row.login,
        role=Role(row.role),
        display_name=row.display_name,
        disabled=row.disabled_at is not None,
    )


class SqlUserRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create(self, login: str, password_hash: str, role: Role, display_name: str) -> User:
        try:
            with self.connection.begin_nested():
                row = self.connection.execute(
                    users.insert()
                    .values(login=login, password_hash=password_hash, role=role.value, display_name=display_name)
                    .returning(users)
                ).one()
        except IntegrityError as error:
            diagnostics = getattr(error.orig, "diag", None)
            if getattr(diagnostics, "constraint_name", None) == USER_LOGIN_INDEX:
                raise PriceWatcherError(ErrorCode.LOGIN_TAKEN) from error
            raise
        return to_user(row)

    def get(self, user_id: int) -> User:
        row = self.connection.execute(select(users).where(users.c.id == user_id)).one_or_none()
        if row is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        return to_user(row)

    def find_credentials(self, login: str) -> UserCredentials | None:
        row = self.connection.execute(select(users).where(func.lower(users.c.login) == login_key(login))).one_or_none()
        return None if row is None else UserCredentials(user=to_user(row), password_hash=row.password_hash)

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        self.connection.execute(update(users).where(users.c.id == user_id).values(password_hash=password_hash))

    def overview(self) -> list[UserOverview]:
        last_login = select(func.max(web_sessions.c.created_at)).where(web_sessions.c.user_id == users.c.id).scalar_subquery()
        rows = self.connection.execute(select(users, last_login.label("last_login_at")).order_by(users.c.id))
        return [
            UserOverview(row.id, row.login, row.display_name, Role(row.role), row.created_at, row.disabled_at, row.last_login_at)
            for row in rows
        ]

    def lock_admins(self) -> list[int]:
        rows = self.connection.execute(
            select(users.c.id).where(users.c.role == Role.ADMIN.value, users.c.disabled_at.is_(None)).order_by(users.c.id).with_for_update()
        )
        return [row.id for row in rows]

    def set_disabled(self, user_id: int, disabled_at: datetime | None) -> None:
        self._changed(update(users).where(users.c.id == user_id).values(disabled_at=disabled_at).returning(users.c.id))

    def set_role(self, user_id: int, role: Role) -> None:
        self._changed(update(users).where(users.c.id == user_id).values(role=role.value).returning(users.c.id))

    def _changed(self, statement: Update) -> None:
        if self.connection.execute(statement).scalar_one_or_none() is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
