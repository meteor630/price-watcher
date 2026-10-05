"""Единица работы на PostgreSQL: одно соединение и одна транзакция на все репозитории.

Выход без commit() — откат, в том числе при исключении.
"""

from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine, RootTransaction

from pricewatch.persistence.app_auth_repositories import (
    SqlDeviceRepository,
    SqlPairingCodeRepository,
    SqlTokenRepository,
)
from pricewatch.persistence.auth_repositories import SqlAuthFailureRepository, SqlSessionRepository
from pricewatch.persistence.invite_repository import SqlInviteRepository
from pricewatch.persistence.shopping_repositories import (
    SqlCategoryRepository,
    SqlGroupRepository,
    SqlItemRepository,
)
from pricewatch.persistence.user_repository import SqlUserRepository


class SqlUnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._connection: Connection | None = None
        self._transaction: RootTransaction | None = None

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("Единица работы уже открыта: вложенный вход не поддерживается")
        self._connection = self.engine.connect()
        self._transaction = self._connection.begin()
        self.users = SqlUserRepository(self._connection)
        self.invites = SqlInviteRepository(self._connection)
        self.auth_failures = SqlAuthFailureRepository(self._connection)
        self.sessions = SqlSessionRepository(self._connection)
        self.pairing_codes = SqlPairingCodeRepository(self._connection)
        self.devices = SqlDeviceRepository(self._connection)
        self.tokens = SqlTokenRepository(self._connection)
        self.categories = SqlCategoryRepository(self._connection)
        self.groups = SqlGroupRepository(self._connection)
        self.items = SqlItemRepository(self._connection)
        return self

    def commit(self) -> None:
        if self._transaction is None or self._connection is None:
            raise RuntimeError("Единица работы не открыта")
        self._transaction.commit()
        self._transaction = self._connection.begin()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._transaction is not None and self._transaction.is_active:
                self._transaction.rollback()
        finally:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._transaction = None
