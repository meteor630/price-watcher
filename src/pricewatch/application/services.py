"""Служебные процессы с доступом к /internal/v1 — Telegram-бот (DEVELOPMENT_ARCHITECTURE.md, разделы 3 и 4).

Токен — как у worker: показывается один раз, в базе только HMAC с назначением SERVICE; ротация через
следующий токен, старый действует, пока сервис не придёт с новым. Каждый запрос отмечает пульс сервиса.
"""

from datetime import datetime

from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import clean_name
from pricewatch.domain.tokens import Purpose, keyed_hash, new_token

SERVICE_NAME_MAX = 80


def create_service(uow: UnitOfWork, key: bytes, name: str, now: datetime) -> str:
    clean = clean_name(name, field="name", max_length=SERVICE_NAME_MAX)
    token = new_token()
    with uow:
        uow.services.create(clean, keyed_hash(key, Purpose.SERVICE, token), now)
        uow.commit()
    return token


def rotate_service_token(uow: UnitOfWork, key: bytes, name: str) -> str:
    token = new_token()
    with uow:
        uow.services.set_next_token(clean_name(name, field="name", max_length=SERVICE_NAME_MAX), keyed_hash(key, Purpose.SERVICE, token))
        uow.commit()
    return token


def authenticate_service(uow: UnitOfWork, key: bytes, token: str | None, now: datetime) -> int:
    if not token:
        raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
    with uow:
        auth = uow.services.find_by_token(keyed_hash(key, Purpose.SERVICE, token))
        if auth is None or auth.disabled:
            raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
        if auth.used_next_token:
            uow.services.promote_next_token(auth.worker_id)
        uow.services.touch(auth.worker_id, now)
        uow.commit()
    return auth.worker_id
