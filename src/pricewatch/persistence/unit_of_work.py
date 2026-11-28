"""Единица работы на PostgreSQL: одно соединение и одна транзакция на все репозитории.

Выход без commit() — откат, в том числе при исключении. Сбой самой базы — недоступность,
обрыв соединения, взаимная блокировка — превращается в DB_ERROR (503); техническая
причина остаётся в цепочке исключений для журнала.
"""

import logging
from types import TracebackType
from typing import Self

from sqlalchemy import Connection, Engine, RootTransaction
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError

from pricewatch.domain.errors import ErrorCode, PriceWatcherError

from pricewatch.persistence.backup_reader_repository import SqlBackupReaderRepository
from pricewatch.persistence.backup_repository import SqlBackupRepository
from pricewatch.persistence.collection_repositories import (
    SqlItemLinkRepository,
    SqlNotificationRepository,
    SqlObservationRepository,
    SqlQueueRepository,
    SqlServiceRepository,
    SqlSettingRepository,
    SqlStoreRepository,
    SqlTrackedLinkRepository,
    SqlWorkerRepository,
)
from pricewatch.persistence.app_auth_repositories import (
    SqlDeviceRepository,
    SqlPairingCodeRepository,
    SqlTokenRepository,
)
from pricewatch.persistence.auth_repositories import SqlAuthFailureRepository, SqlSessionRepository
from pricewatch.persistence.idempotency_repository import SqlIdempotencyRepository
from pricewatch.persistence.invite_repository import SqlInviteRepository
from pricewatch.persistence.price_data_repository import SqlPriceDataRepository
from pricewatch.persistence.review_repositories import SqlAuditRepository, SqlClientErrorRepository, SqlDecisionRepository
from pricewatch.persistence.shopping_repositories import (
    SqlCategoryRepository,
    SqlGroupRepository,
    SqlItemRepository,
    SqlPersonalDataRepository,
)
from pricewatch.persistence.telegram_repository import SqlTelegramRepository
from pricewatch.persistence.user_repository import SqlUserRepository

logger = logging.getLogger(__name__)

# Сбои базы, а не ошибки запросов: их причина — среда, и повтор позже может помочь.
DATABASE_FAILURES = (OperationalError, InterfaceError)


class SqlUnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._connection: Connection | None = None
        self._transaction: RootTransaction | None = None

    def __enter__(self) -> Self:
        if self._connection is not None:
            raise RuntimeError("Единица работы уже открыта: вложенный вход не поддерживается")
        try:
            self._connection = self.engine.connect()
            self._transaction = self._connection.begin()
        except DATABASE_FAILURES as error:
            self._connection = None
            raise PriceWatcherError(ErrorCode.DB_ERROR) from error
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
        self.personal_data = SqlPersonalDataRepository(self._connection)
        self.stores = SqlStoreRepository(self._connection)
        self.tracked_links = SqlTrackedLinkRepository(self._connection)
        self.item_links = SqlItemLinkRepository(self._connection)
        self.observations = SqlObservationRepository(self._connection)
        self.queue = SqlQueueRepository(self._connection)
        self.workers = SqlWorkerRepository(self._connection)
        self.price_data = SqlPriceDataRepository(self._connection)
        self.idempotency = SqlIdempotencyRepository(self._connection)
        self.settings = SqlSettingRepository(self._connection)
        self.notifications = SqlNotificationRepository(self._connection)
        self.decisions = SqlDecisionRepository(self._connection)
        self.audit = SqlAuditRepository(self._connection)
        self.client_errors = SqlClientErrorRepository(self._connection)
        self.services = SqlServiceRepository(self._connection)
        self.telegram = SqlTelegramRepository(self._connection)
        self.backups = SqlBackupRepository(self._connection)
        self.backup_readers = SqlBackupReaderRepository(self._connection)
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
        except SQLAlchemyError:
            if not isinstance(exc, DATABASE_FAILURES):
                raise
            # Соединение уже мёртвое: откат невозможен, база сама откатит транзакцию при разрыве.
            logger.warning("Откат после сбоя базы не удался", exc_info=True)
        finally:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._transaction = None
        if isinstance(exc, DATABASE_FAILURES):
            raise PriceWatcherError(ErrorCode.DB_ERROR) from exc
