"""Telegram (DEVELOPMENT_ARCHITECTURE.md, разделы 4 и 10).

- Привязка: на сайте пользователь получает одноразовый код (15 минут, в базе только HMAC) и отправляет его
  боту командой /start; бот передаёт код и чат. Один Telegram — один пользователь: чат, привязанный к другому,
  переходит к новому. Отвязать можно на сайте.
- Код для приложения по команде боту — только привязанному и незаблокированному пользователю.
- Сообщения: бот забирает неотправленные Telegram-уведомления (захват на 2 минуты — второй экземпляр бота
  их не возьмёт), отмечает отправку или сбой; после сбоя повтор через 2 минуты, не больше 30 попыток.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.application.app_auth import IssuedPairingCode, issue_pairing_code
from pricewatch.application.notifications import notification_text
from pricewatch.application.ports import AuditEvent, UnitOfWork
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, keyed_hash, new_token

LINK_CODE_LIFETIME = timedelta(minutes=15)
CLAIM_FOR = timedelta(minutes=2)
RETRY_AFTER = timedelta(minutes=2)
SEND_ATTEMPTS_MAX = 30
BATCH_MAX = 50


@dataclass(frozen=True, slots=True)
class IssuedLinkCode:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TelegramMessage:
    notification_id: int
    chat_id: int
    text: str


def issue_link_code(uow: UnitOfWork, key: bytes, user: User, now: datetime) -> IssuedLinkCode:
    code = new_token()[:22]
    with uow:
        uow.telegram.create_link_code(user.id, keyed_hash(key, Purpose.TELEGRAM_LINK, code), now, now + LINK_CODE_LIFETIME)
        uow.commit()
    return IssuedLinkCode(code, now + LINK_CODE_LIFETIME)


def is_linked(uow: UnitOfWork, user: User) -> bool:
    with uow:
        return uow.telegram.is_linked(user.id)


def unlink(uow: UnitOfWork, user: User, now: datetime) -> None:
    with uow:
        uow.telegram.clear_chat(user.id)
        uow.audit.add(AuditEvent(user.id, "telegram_unlinked", "user", user.id, None, None, now))
        uow.commit()


def link_chat(uow: UnitOfWork, key: bytes, code: str, chat_id: int, now: datetime) -> User:
    """Привязать чат по коду с сайта; неизвестный, использованный или истёкший код — NOT_FOUND."""
    with uow:
        user_id = uow.telegram.use_link_code(keyed_hash(key, Purpose.TELEGRAM_LINK, code), now)
        if user_id is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        user = uow.users.get(user_id)
        if user.disabled:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        uow.telegram.set_chat(user_id, chat_id)
        uow.audit.add(AuditEvent(user_id, "telegram_linked", "user", user_id, None, None, now))
        uow.commit()
    return user


def pairing_code_for_chat(uow_factory: Callable[[], UnitOfWork], key: bytes, chat_id: int, now: datetime) -> IssuedPairingCode:
    """Код для приложения по команде боту; чат не привязан или пользователь заблокирован — NOT_FOUND."""
    with uow_factory() as uow:
        user = uow.telegram.user_by_chat(chat_id)
    if user is None or user.disabled:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return issue_pairing_code(uow_factory(), key, user.id, now)


def claim_messages(uow: UnitOfWork, now: datetime, limit: int) -> list[TelegramMessage]:
    with uow:
        claimed = uow.telegram.claim_outbox(now, now + CLAIM_FOR, min(limit, BATCH_MAX), SEND_ATTEMPTS_MAX)
        uow.commit()
    messages = []
    for message in claimed:
        text, _ = notification_text(message.kind, message.payload)
        if message.repeat_count > 1:
            text += f" Повторилось {message.repeat_count} раз."
        messages.append(TelegramMessage(message.notification_id, message.chat_id, text))
    return messages


def message_sent(uow: UnitOfWork, notification_id: int, now: datetime) -> None:
    with uow:
        if not uow.telegram.mark_sent(notification_id, now):
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        uow.commit()


def message_failed(uow: UnitOfWork, notification_id: int, now: datetime) -> None:
    with uow:
        if not uow.telegram.mark_failed(notification_id, now + RETRY_AFTER):
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        uow.commit()
