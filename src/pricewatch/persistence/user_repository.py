"""Пользователи на PostgreSQL. Правила — в pricewatch.application.ports.UserRepository."""

from sqlalchemy import Connection, Row, Table, select
from sqlalchemy.exc import IntegrityError

from pricewatch.domain.accounts import Role, User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.models import USER_LOGIN_INDEX, UserRow

users: Table = UserRow.__table__


def _to_user(row: Row) -> User:
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
        return _to_user(row)

    def get(self, user_id: int) -> User:
        row = self.connection.execute(select(users).where(users.c.id == user_id)).one_or_none()
        if row is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        return _to_user(row)
