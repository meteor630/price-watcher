"""Вход в приложение: коды связки, устройства, ротация refresh-токенов (раздел 4 архитектуры).

- Код связки: 8 символов, 5 минут, одноразовый; неверные вводы ограничены по IP.
- Первая пара токенов случайна; каждая следующая выводится из предыдущего refresh-токена
  через HMAC. Поэтому повтор того же refresh-токена в течение 60 секунд пересчитывает
  ту же пару, ничего не храня в открытом виде.
- Повтор позже окна — признак кражи: устройство (семья токенов) отзывается, и отзыв
  фиксируется до ответа об ошибке, чтобы откат транзакции его не отменил.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.accounts import (
    ACCESS_TOKEN_LIFETIME,
    FAILURE_WINDOW,
    PAIRING_CODE_LIFETIME,
    PAIRING_FAILURES_PER_IP,
    REFRESH_REPLAY_WINDOW,
    REFRESH_TOKEN_LIFETIME,
    Device,
    TokenPair,
    User,
    check_device_name,
    retry_after_seconds,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, derive_token, keyed_hash, new_pairing_code, new_token

logger = logging.getLogger(__name__)

PAIRING_FAILURE = "pairing"


@dataclass(frozen=True, slots=True)
class IssuedPairingCode:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeviceUser:
    user: User
    device_id: int


def issue_pairing_code(uow: UnitOfWork, key: bytes, user_id: int, now: datetime) -> IssuedPairingCode:
    """Код для приложения; показывается один раз, в базе только HMAC."""
    code = new_pairing_code()
    with uow:
        if uow.users.get(user_id).disabled:
            raise PriceWatcherError(ErrorCode.FORBIDDEN)
        expires_at = now + PAIRING_CODE_LIFETIME
        uow.pairing_codes.create(user_id, keyed_hash(key, Purpose.PAIRING_CODE, code), now, expires_at)
        uow.commit()
    return IssuedPairingCode(code=code, expires_at=expires_at)


def pair(uow: UnitOfWork, key: bytes, code: str, device_name: str, ip: str | None, now: datetime) -> tuple[Device, TokenPair]:
    name = check_device_name(device_name)
    with uow:
        if ip is not None:
            window = uow.auth_failures.window_for_ip(PAIRING_FAILURE, ip, now - FAILURE_WINDOW)
            if window.count >= PAIRING_FAILURES_PER_IP and window.oldest is not None:
                retry_after = retry_after_seconds(window.oldest, now)
                raise PriceWatcherError(ErrorCode.RATE_LIMITED, {"retry_after_seconds": retry_after})
        claim = uow.pairing_codes.claim(keyed_hash(key, Purpose.PAIRING_CODE, code))
        problem = None
        if claim is None or claim.used:
            problem = ErrorCode.PAIRING_CODE_INVALID
        elif claim.expires_at <= now:
            problem = ErrorCode.PAIRING_CODE_EXPIRED
        elif uow.users.get(claim.user_id).disabled:
            problem = ErrorCode.PAIRING_CODE_INVALID
        if claim is None or problem is not None:
            uow.auth_failures.record(PAIRING_FAILURE, None, ip, now)
            uow.commit()
            raise PriceWatcherError(problem or ErrorCode.PAIRING_CODE_INVALID)

        device = uow.devices.create(claim.user_id, name, now)
        tokens = _store_pair(uow, key, device.id, new_token(), new_token(), now)
        uow.pairing_codes.mark_used(claim.id, now)
        uow.commit()
    return device, tokens


def refresh(uow: UnitOfWork, key: bytes, refresh_token: str, now: datetime) -> TokenPair:
    with uow:
        record = uow.tokens.find_refresh(keyed_hash(key, Purpose.REFRESH, refresh_token))
        if record is None or record.expires_at <= now:
            raise PriceWatcherError(ErrorCode.REFRESH_TOKEN_INVALID)
        device = uow.devices.get_active(record.device_id)
        if device is None or uow.users.get(device.user_id).disabled:
            raise PriceWatcherError(ErrorCode.REFRESH_TOKEN_INVALID)

        next_refresh = derive_token(key, Purpose.REFRESH, refresh_token)
        next_access = derive_token(key, Purpose.ACCESS, refresh_token)
        if record.used_at is None:
            uow.tokens.mark_refresh_used(record.id, now)
            uow.devices.touch(device.id, now)
            issued = _store_pair(uow, key, device.id, next_access, next_refresh, now)
            uow.commit()
            return issued
        if now - record.used_at <= REFRESH_REPLAY_WINDOW:
            return _stored_pair(uow, key, next_access, next_refresh)

        uow.devices.revoke(device.id, now)
        uow.commit()
    logger.warning(
        "Повтор refresh-токена вне окна: устройство отозвано",
        extra={"device_id": device.id, "user_id": device.user_id},
    )
    raise PriceWatcherError(ErrorCode.REFRESH_TOKEN_REUSED)


def authenticate_access(uow: UnitOfWork, key: bytes, access_token: str | None, now: datetime) -> DeviceUser:
    """Пользователь по access-токену или AUTH_REQUIRED: нет, истёк, устройство отозвано, пользователь отключён."""
    if not access_token:
        raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
    with uow:
        grant = uow.tokens.find_access(keyed_hash(key, Purpose.ACCESS, access_token), now)
        if grant is None:
            raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
        user = uow.users.get(grant.user_id)
        if user.disabled:
            raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
    return DeviceUser(user=user, device_id=grant.device_id)


def logout_device(uow: UnitOfWork, device_id: int, now: datetime) -> None:
    """Отзывает устройство: все его access- и refresh-токены перестают работать сразу."""
    with uow:
        uow.devices.revoke(device_id, now)
        uow.commit()


def _store_pair(uow: UnitOfWork, key: bytes, device_id: int, access: str, refresh_token: str, now: datetime) -> TokenPair:
    access_expires = now + ACCESS_TOKEN_LIFETIME
    refresh_expires = now + REFRESH_TOKEN_LIFETIME
    uow.tokens.add_access(device_id, keyed_hash(key, Purpose.ACCESS, access), now, access_expires)
    uow.tokens.add_refresh(device_id, keyed_hash(key, Purpose.REFRESH, refresh_token), now, refresh_expires)
    return TokenPair(access, access_expires, refresh_token, refresh_expires)


def _stored_pair(uow: UnitOfWork, key: bytes, access: str, refresh_token: str) -> TokenPair:
    """Та же пара, что выдана при первом использовании: сроки берутся из базы, а не считаются заново."""
    access_expires = uow.tokens.expires_of_access(keyed_hash(key, Purpose.ACCESS, access))
    refresh_expires = uow.tokens.expires_of_refresh(keyed_hash(key, Purpose.REFRESH, refresh_token))
    if access_expires is None or refresh_expires is None:
        raise RuntimeError("Выданная пара не найдена в базе: нарушена согласованность ротации")
    return TokenPair(access, access_expires, refresh_token, refresh_expires)
