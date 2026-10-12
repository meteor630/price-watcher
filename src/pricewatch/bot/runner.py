"""Цикл бота: рассылка уведомлений с сервера → длинный опрос Telegram (до 10 с) → ответы на команды.

- Каждое обновление подтверждается (offset) после ответа: команда не выполняется дважды, даже если ответ
  не ушёл — привязка по одноразовому коду при повторе ответила бы «код не подошёл».
- Уведомление: взято с сервера → отправлено → «отправлено»; чат недоступен или Telegram сбоит → «сбой»,
  сервер вернёт его позже (до 30 попыток). Telegram недоступен — остальные взятые ждут конца захвата.
- Telegram или сервер недоступны — пауза и повтор; неверный токен бота или сервиса останавливает бота.
- Сервер не отвечает 3 минуты — предупреждение администраторам (bot/watch.py); второй экземпляр бота,
  которому Telegram отвечает «конфликт», не предупреждает.
- Неожиданная ошибка обхода — в журнал с трассировкой, бот живёт дальше.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from pricewatch.bot import commands
from pricewatch.bot.api import ApiUnavailable, NotFound, Outgoing, PairingCode, ServiceRejected
from pricewatch.bot.telegram import ChatUnreachable, TelegramConflict, TelegramRejected, TelegramUnavailable, Update
from pricewatch.bot.watch import ServerWatch

logger = logging.getLogger(__name__)

POLL_S = 10
OUTBOX_BATCH = 20
TELEGRAM_RETRY_S = 5.0
API_RETRY_S = 15.0


class Api(Protocol):
    def link(self, code: str, chat_id: int) -> str: ...
    def pairing_code(self, chat_id: int) -> PairingCode: ...
    def claim(self, limit: int) -> list[Outgoing]: ...
    def sent(self, notification_id: int) -> None: ...
    def failed(self, notification_id: int) -> None: ...


class Telegram(Protocol):
    def updates(self, offset: int | None, poll_s: int) -> list[Update]: ...
    def send(self, chat_id: int, text: str) -> None: ...
    def set_commands(self, commands: list[tuple[str, str]]) -> None: ...


class Bot:
    def __init__(
        self,
        api: Api,
        telegram: Telegram,
        *,
        admin_chats: Sequence[int] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        wait: Callable[[float], bool] | None = None,
        poll_s: int = POLL_S,
    ) -> None:
        self._api = api
        self._telegram = telegram
        self._clock = clock
        self._watch = ServerWatch(admin_chats, clock())
        # Этот экземпляр получает обновления из Telegram (у второго — «конфликт»); только он предупреждает.
        self._leading = False
        self._stopping = threading.Event()
        self._wait = wait or self._stopping.wait
        self._poll_s = poll_s
        self._offset: int | None = None
        self._commands_set = False

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                pause = self.tick()
            except (ServiceRejected, TelegramRejected):
                raise
            except Exception:
                # Бот живёт дальше: следующий обход повторит то, что не удалось.
                logger.exception("Сбой обхода бота")
                pause = TELEGRAM_RETRY_S
            if pause:
                self._wait(pause)

    def tick(self) -> float:
        """Один обход; сколько секунд подождать перед следующим (0 — сразу). Сервер — первым: неверный токен
        сервиса останавливает бота сразу после запуска."""
        delivered = self._deliver()
        self._set_commands()
        received = self._receive()
        self._watch.check(self._telegram, self._clock(), self._leading)
        return max(received, delivered)

    def _set_commands(self) -> None:
        if self._commands_set:
            return
        try:
            self._telegram.set_commands(commands.COMMANDS)
        except TelegramUnavailable as error:
            logger.warning("Меню команд не обновлено, повтор на следующем обходе", extra={"reason": str(error)})
            return
        self._commands_set = True

    def _receive(self) -> float:
        try:
            updates = self._telegram.updates(self._offset, self._poll_s)
        except TelegramConflict:
            if self._leading:
                logger.warning("Обновления получает другой экземпляр бота; этот только рассылает уведомления")
            self._leading = False
            return TELEGRAM_RETRY_S
        except TelegramUnavailable as error:
            logger.warning("Telegram не отдал обновления", extra={"reason": str(error)})
            return float(error.retry_after_s or TELEGRAM_RETRY_S)
        self._leading = True
        for update in updates:
            self._offset = update.update_id + 1
            if update.chat_id is None or update.text is None:
                continue
            answer = commands.reply(self._api, update.chat_id, update.text)
            try:
                self._telegram.send(update.chat_id, answer)
            except (ChatUnreachable, TelegramUnavailable) as error:
                logger.warning("Ответ на команду не отправлен", extra={"reason": str(error)})
        return 0.0

    def _deliver(self) -> float:
        try:
            batch = self._api.claim(OUTBOX_BATCH)
        except ApiUnavailable as error:
            logger.warning("Сервер не отдал сообщения на отправку", extra={"reason": str(error)})
            return API_RETRY_S
        self._watch.answered(self._clock())
        for message in batch:
            try:
                pause = self._send(message)
            except ApiUnavailable as error:
                logger.warning("Сервер не принял отметку сообщения", extra={"notification_id": message.notification_id, "reason": str(error)})
                return API_RETRY_S
            except NotFound:
                # Захват истёк и сообщение уже отмечено другим экземпляром бота.
                logger.warning("Сообщение уже отмечено", extra={"notification_id": message.notification_id})
                continue
            if pause:
                return pause
        return 0.0

    def _send(self, message: Outgoing) -> float:
        try:
            self._telegram.send(message.chat_id, message.text)
        except ChatUnreachable as error:
            logger.warning("Чат недоступен, сообщение вернётся позже", extra={"notification_id": message.notification_id, "reason": str(error)})
            self._api.failed(message.notification_id)
            return 0.0
        except TelegramUnavailable as error:
            logger.warning("Telegram не принял сообщение, повтор позже", extra={"notification_id": message.notification_id, "reason": str(error)})
            self._api.failed(message.notification_id)
            return float(error.retry_after_s or TELEGRAM_RETRY_S)
        self._api.sent(message.notification_id)
        return 0.0
