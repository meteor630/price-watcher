"""Домашний приёмник копий (DEVELOPMENT_ARCHITECTURE.md, раздел 11): /backups/v1, только чтение копий.

Сервер в дом не ходит: приёмник сам берёт список копий, скачивает новые, сверяет sha256 и подтверждает получение.
- Токен — как у worker: показывается один раз, в базе только HMAC с назначением BACKUP_READER; ротация через
  следующий токен, старый действует, пока приёмник не придёт с новым. Токены worker, бота и приложения здесь не действуют.
- Неверный токен записывается как неудача по IP; 10 неудач за 15 минут — RATE_LIMITED с временем до следующей попытки.
- Подтверждение получения (confirm_received) — отметка для сторожа: дом не забирал копии больше 36 часов — уведомление.
"""

from datetime import datetime

from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.accounts import FAILURE_WINDOW, retry_after_seconds
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import clean_name
from pricewatch.domain.tokens import Purpose, keyed_hash, new_token

READER_NAME_MAX = 80
BACKUP_FAILURE = "backup"
BACKUP_FAILURES_PER_IP = 10


def create_backup_reader(uow: UnitOfWork, key: bytes, name: str, now: datetime) -> str:
    clean = clean_name(name, field="name", max_length=READER_NAME_MAX)
    token = new_token()
    with uow:
        uow.backup_readers.create(clean, keyed_hash(key, Purpose.BACKUP_READER, token), now)
        uow.commit()
    return token


def rotate_backup_reader_token(uow: UnitOfWork, key: bytes, name: str) -> str:
    token = new_token()
    with uow:
        uow.backup_readers.set_next_token(
            clean_name(name, field="name", max_length=READER_NAME_MAX), keyed_hash(key, Purpose.BACKUP_READER, token)
        )
        uow.commit()
    return token


def authenticate_backup_reader(uow: UnitOfWork, key: bytes, token: str | None, ip: str | None, now: datetime) -> int:
    with uow:
        if ip is not None:
            window = uow.auth_failures.window_for_ip(BACKUP_FAILURE, ip, now - FAILURE_WINDOW)
            if window.count >= BACKUP_FAILURES_PER_IP and window.oldest is not None:
                raise PriceWatcherError(ErrorCode.RATE_LIMITED, {"retry_after_seconds": retry_after_seconds(window.oldest, now)})
        auth = uow.backup_readers.find_by_token(keyed_hash(key, Purpose.BACKUP_READER, token)) if token else None
        if auth is None or auth.disabled:
            uow.auth_failures.record(BACKUP_FAILURE, None, ip, now)
            uow.commit()
            raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
        if auth.used_next_token:
            uow.backup_readers.promote_next_token(auth.worker_id)
        uow.backup_readers.touch(auth.worker_id, now)
        uow.commit()
    return auth.worker_id


def confirm_received(uow: UnitOfWork, reader_id: int, now: datetime) -> None:
    with uow:
        uow.backup_readers.mark_pulled(reader_id, now)
        uow.commit()
