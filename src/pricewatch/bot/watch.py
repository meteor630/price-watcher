"""Сторож на стороне бота (DEVELOPMENT_ARCHITECTURE.md, раздел 1): сервер не отвечает 3 минуты — бот сам пишет
администраторам из своих настроек (сервер недоступен — спросить список не у кого); ответил снова — сообщение
о восстановлении тем, кого предупредили.

- Ответ сервера — любой удачный запрос бота (он обращается к серверу на каждом обходе).
- Предупреждает только экземпляр, который сейчас получает обновления из Telegram: у второго запущенного бота
  Telegram отвечает «конфликт», и он молчит — администратор не получает двух одинаковых сообщений.
- Каждому администратору — одно предупреждение на инцидент; Telegram недоступен — повтор на следующем обходе;
  чат недоступен (неверный chat id) — в журнал, без повторов.
"""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from pricewatch.bot.telegram import ChatUnreachable, TelegramUnavailable
from pricewatch.domain.days import MOSCOW

logger = logging.getLogger(__name__)

SERVER_SILENCE = timedelta(minutes=3)
SERVER_DOWN = "Сервер Price Watcher не отвечает с {since} по Москве: сайт, приложение и проверка цен, скорее всего, недоступны."
SERVER_BACK = "Сервер Price Watcher снова отвечает (не отвечал с {since} по Москве)."


class Sender(Protocol):
    def send(self, chat_id: int, text: str) -> None: ...


class ServerWatch:
    def __init__(self, admin_chats: Sequence[int], now: datetime) -> None:
        self._admins = tuple(admin_chats)
        self._answered_at = now
        self._warned: set[int] = set()
        self._recovery: set[int] = set()
        self._recovery_since = now

    def answered(self, now: datetime) -> None:
        if self._warned:
            logger.info("Сервер снова отвечает", extra={"since": self._answered_at.isoformat()})
            self._recovery |= self._warned
            self._recovery_since = self._answered_at
            self._warned.clear()
        self._answered_at = now

    def check(self, telegram: Sender, now: datetime, leading: bool) -> None:
        if self._recovery:
            self._recovery -= _send_each(telegram, sorted(self._recovery), SERVER_BACK.format(since=_moment(self._recovery_since)))
        if not leading or now - self._answered_at < SERVER_SILENCE:
            return
        waiting = [chat for chat in self._admins if chat not in self._warned]
        if waiting:
            logger.warning("Сервер не отвечает, администраторы предупреждаются", extra={"since": self._answered_at.isoformat()})
            self._warned |= _send_each(telegram, waiting, SERVER_DOWN.format(since=_moment(self._answered_at)))


def _send_each(telegram: Sender, chats: Sequence[int], text: str) -> set[int]:
    """Чаты, с которыми покончено: сообщение ушло или чат недоступен. Telegram недоступен — остальные позже."""
    done: set[int] = set()
    for chat in chats:
        try:
            telegram.send(chat, text)
        except ChatUnreachable as error:
            logger.error("Чат администратора недоступен: проверьте PRICEWATCH_BOT_ADMIN_CHAT_IDS", extra={"chat_id": chat, "reason": str(error)})
        except TelegramUnavailable as error:
            logger.warning("Telegram не принял сообщение сторожа, повтор позже", extra={"reason": str(error)})
            break
        done.add(chat)
    return done


def _moment(moment: datetime) -> str:
    return f"{moment.astimezone(MOSCOW):%d.%m в %H:%M}"
