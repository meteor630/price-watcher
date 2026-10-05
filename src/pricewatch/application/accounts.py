"""Сценарии учётных записей: администратор, приглашения, регистрация."""

from datetime import datetime

from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.accounts import (
    INVITE_LIFETIME,
    IssuedInvite,
    Role,
    User,
    check_display_name,
    check_login,
    check_password,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, keyed_hash, new_token


def create_admin(uow: UnitOfWork, hasher: PasswordHasher, login: str, password: str, display_name: str) -> User:
    """Первый и следующие администраторы создаются командой на сервере (раздел 4 архитектуры)."""
    clean_login = check_login(login)
    clean_password = check_password(password, clean_login)
    name = check_display_name(display_name)
    with uow:
        user = uow.users.create(clean_login, hasher.hash(clean_password), Role.ADMIN, name)
        uow.commit()
    return user


def create_invite(uow: UnitOfWork, key: bytes, actor_id: int, role: Role, now: datetime) -> IssuedInvite:
    """Одноразовое приглашение на 7 дней. Код возвращается один раз; в базе только его HMAC."""
    code = new_token()
    with uow:
        actor = uow.users.get(actor_id)
        if not actor.is_admin or actor.disabled:
            raise PriceWatcherError(ErrorCode.FORBIDDEN)
        invite = uow.invites.create(keyed_hash(key, Purpose.INVITE, code), role, actor.id, now, now + INVITE_LIFETIME)
        uow.commit()
    return IssuedInvite(invite=invite, code=code)


def register(
    uow: UnitOfWork,
    hasher: PasswordHasher,
    key: bytes,
    code: str,
    login: str,
    password: str,
    display_name: str,
    now: datetime,
) -> User:
    """Регистрация по приглашению.

    Сначала проверяются поля: неверные данные не тратят приглашение. Затем приглашение
    блокируется до конца транзакции, поэтому две регистрации по одному коду идут
    по очереди и вторая получает INVITE_INVALID. Использованное, истёкшее и выдуманное
    приглашения неотличимы.
    """
    clean_login = check_login(login)
    password_hash = hasher.hash(check_password(password, clean_login))
    name = check_display_name(display_name)
    with uow:
        invite = uow.invites.claim(keyed_hash(key, Purpose.INVITE, code), now)
        if invite is None:
            raise PriceWatcherError(ErrorCode.INVITE_INVALID)
        user = uow.users.create(clean_login, password_hash, invite.role, name)
        uow.invites.mark_used(invite.id, user.id, now)
        uow.commit()
    return user
