"""Пользователи на PostgreSQL. Правила — в pricewatch.application.ports.UserRepository."""

from sqlalchemy import Connection, Row, Table, func, select, update
from sqlalchemy.exc import IntegrityError

from pricewatch.application.ports import UserCredentials
from pricewatch.domain.accounts import Role, User, login_key
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

    def find_credentials(self, login: str) -> UserCredentials | None:
        row = self.connection.execute(select(users).where(func.lower(users.c.login) == login_key(login))).one_or_none()
        return None if row is None else UserCredentials(user=_to_user(row), password_hash=row.password_hash)

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        self.connection.execute(update(users).where(users.c.id == user_id).values(password_hash=password_hash))
