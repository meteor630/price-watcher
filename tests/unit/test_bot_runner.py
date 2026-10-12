"""Бот с подменёнными сервером и Telegram: команды и ответы, подтверждение обновлений, рассылка с отметками,
паузы при недоступности, остановка при неверном токене, неожиданная ошибка не останавливает бота."""

import logging
from datetime import UTC, datetime

import pytest

from pricewatch.bot import commands
from pricewatch.bot.api import ApiUnavailable, NotFound, Outgoing, PairingCode, ServiceRejected
from pricewatch.bot.runner import API_RETRY_S, OUTBOX_BATCH, TELEGRAM_RETRY_S, Bot
from pricewatch.bot.telegram import ChatUnreachable, TelegramConflict, TelegramRejected, TelegramUnavailable, Update

CHAT = 77


class Api:
    def __init__(self) -> None:
        self.links: list[tuple[str, int]] = []
        self.outbox: list[Outgoing] = []
        self.marked_sent: list[int] = []
        self.marked_failed: list[int] = []
        self.claims: list[int] = []
        self.link_error: Exception | None = None
        self.code_error: Exception | None = None
        self.claim_error: Exception | None = None
        self.mark_error: Exception | None = None

    def link(self, code: str, chat_id: int) -> str:
        if self.link_error is not None:
            raise self.link_error
        self.links.append((code, chat_id))
        return "<b>Аня</b>"

    def pairing_code(self, chat_id: int) -> PairingCode:
        if self.code_error is not None:
            raise self.code_error
        return PairingCode("HMRPYF5P", datetime(2026, 10, 12, 9, 5, tzinfo=UTC))

    def claim(self, limit: int) -> list[Outgoing]:
        self.claims.append(limit)
        if self.claim_error is not None:
            raise self.claim_error
        batch, self.outbox = self.outbox, []
        return batch

    def sent(self, notification_id: int) -> None:
        if self.mark_error is not None:
            raise self.mark_error
        self.marked_sent.append(notification_id)

    def failed(self, notification_id: int) -> None:
        if self.mark_error is not None:
            raise self.mark_error
        self.marked_failed.append(notification_id)


class FakeTelegram:
    def __init__(self, updates: list[list[Update] | Exception] | None = None) -> None:
        self.batches = list(updates or [])
        self.offsets: list[int | None] = []
        self.messages: list[tuple[int, str]] = []
        self.send_errors: dict[int, Exception] = {}
        self.commands: list[list[tuple[str, str]]] = []
        self.commands_error: Exception | None = None

    def updates(self, offset: int | None, poll_s: int) -> list[Update]:
        self.offsets.append(offset)
        batch = self.batches.pop(0) if self.batches else []
        if isinstance(batch, Exception):
            raise batch
        return batch

    def send(self, chat_id: int, text: str) -> None:
        error = self.send_errors.pop(chat_id, None)
        if error is not None:
            raise error
        self.messages.append((chat_id, text))

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        if self.commands_error is not None:
            raise self.commands_error
        self.commands.append(commands)


def bot(api: Api, telegram: FakeTelegram) -> Bot:
    return Bot(api, telegram, wait=lambda seconds: False)


def say(text: str, update_id: int = 1, chat: int | None = CHAT) -> Update:
    return Update(update_id, chat, text if chat is not None else None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start abc_DEF-123", commands.LINKED.format(name="<b>Аня</b>")),
        ("/code", commands.PAIRING.format(code="HMRP YF5P", until="12:05")),
        ("/code@price_watcher_bot", commands.PAIRING.format(code="HMRP YF5P", until="12:05")),
        ("/start", commands.HELP),
        ("/start код с пробелами", commands.HELP),
        ("/start " + "x" * 65, commands.HELP),
        ("/help", commands.HELP),
        ("привет", commands.HELP),
    ],
)
def test_command_replies(text: str, expected: str) -> None:
    api = Api()

    assert commands.reply(api, CHAT, text) == expected


def test_start_passes_code_and_chat() -> None:
    api = Api()

    commands.reply(api, CHAT, "  /start abc_DEF-123  ")

    assert api.links == [("abc_DEF-123", CHAT)]


@pytest.mark.parametrize(
    ("text", "error", "expected"),
    [
        ("/start abc", NotFound("link"), commands.BAD_LINK_CODE),
        ("/code", NotFound("code"), commands.NOT_LINKED),
        ("/start abc", ApiUnavailable("down"), commands.SERVER_DOWN),
        ("/code", ApiUnavailable("down"), commands.SERVER_DOWN),
    ],
)
def test_command_refusals(text: str, error: Exception, expected: str) -> None:
    api = Api()
    api.link_error = api.code_error = error

    assert commands.reply(api, CHAT, text) == expected


def test_updates_are_answered_and_confirmed() -> None:
    api, telegram = Api(), FakeTelegram([[say("/code", 41), say("", 42, chat=None)], []])
    worker = bot(api, telegram)

    worker.tick()
    worker.tick()

    assert telegram.offsets == [None, 43]
    assert telegram.messages == [(CHAT, commands.PAIRING.format(code="HMRP YF5P", until="12:05"))]
    assert telegram.commands == [commands.COMMANDS]


def test_failed_reply_still_confirms_update(caplog: pytest.LogCaptureFixture) -> None:
    api, telegram = Api(), FakeTelegram([[say("/start abc", 5)], []])
    telegram.send_errors[CHAT] = ChatUnreachable("sendMessage: HTTP 403")
    worker = bot(api, telegram)

    with caplog.at_level(logging.WARNING):
        worker.tick()
        worker.tick()

    assert api.links == [("abc", CHAT)] and telegram.offsets == [None, 6]
    assert any(record.getMessage() == "Ответ на команду не отправлен" for record in caplog.records)


def test_outbox_is_sent_and_marked() -> None:
    api, telegram = Api(), FakeTelegram()
    api.outbox = [Outgoing(1, 10, "Сводка"), Outgoing(2, 20, "Ещё")]

    pause = bot(api, telegram).tick()

    assert pause == 0 and api.claims == [OUTBOX_BATCH]
    assert telegram.messages == [(10, "Сводка"), (20, "Ещё")] and api.marked_sent == [1, 2]


def test_unreachable_chat_is_failed_and_others_go_on() -> None:
    api, telegram = Api(), FakeTelegram()
    api.outbox = [Outgoing(1, 10, "Сводка"), Outgoing(2, 20, "Ещё")]
    telegram.send_errors[10] = ChatUnreachable("sendMessage: HTTP 403")

    bot(api, telegram).tick()

    assert api.marked_failed == [1] and api.marked_sent == [2]


def test_telegram_down_fails_message_and_pauses_rest() -> None:
    api, telegram = Api(), FakeTelegram()
    api.outbox = [Outgoing(1, 10, "Сводка"), Outgoing(2, 20, "Ещё")]
    telegram.send_errors[10] = TelegramUnavailable("sendMessage: HTTP 429", retry_after_s=30)

    pause = bot(api, telegram).tick()

    assert api.marked_failed == [1] and api.marked_sent == [] and telegram.messages == []
    assert pause == 30


def test_server_down_pauses_and_bot_still_answers(caplog: pytest.LogCaptureFixture) -> None:
    api, telegram = Api(), FakeTelegram([[say("/code", 1)]])
    api.claim_error = ApiUnavailable("/internal/v1/outbox/claim: ConnectError")
    api.code_error = ApiUnavailable("down")

    with caplog.at_level(logging.WARNING):
        pause = bot(api, telegram).tick()

    assert pause == API_RETRY_S and telegram.messages == [(CHAT, commands.SERVER_DOWN)]
    assert any(record.getMessage() == "Сервер не отдал сообщения на отправку" for record in caplog.records)


def test_mark_lost_on_server_stops_batch() -> None:
    api, telegram = Api(), FakeTelegram()
    api.outbox = [Outgoing(1, 10, "Сводка"), Outgoing(2, 20, "Ещё")]
    api.mark_error = ApiUnavailable("down")

    assert bot(api, telegram).tick() == API_RETRY_S
    assert telegram.messages == [(10, "Сводка")]


def test_already_marked_message_is_skipped() -> None:
    api, telegram = Api(), FakeTelegram()
    api.outbox = [Outgoing(1, 10, "Сводка")]
    api.mark_error = NotFound("/internal/v1/outbox/1/sent")

    assert bot(api, telegram).tick() == 0


@pytest.mark.parametrize("error", [TelegramUnavailable("getUpdates: ConnectError"), TelegramConflict("getUpdates: HTTP 409")])
def test_telegram_down_pauses_polling(error: Exception) -> None:
    api, telegram = Api(), FakeTelegram([error])

    assert bot(api, telegram).tick() == TELEGRAM_RETRY_S


def test_commands_menu_is_retried_until_set() -> None:
    api, telegram = Api(), FakeTelegram()
    telegram.commands_error = TelegramUnavailable("setMyCommands: HTTP 502")
    worker = bot(api, telegram)

    worker.tick()
    telegram.commands_error = None
    worker.tick()
    worker.tick()

    assert telegram.commands == [commands.COMMANDS]


@pytest.mark.parametrize("error", [ServiceRejected("/internal/v1/outbox/claim"), TelegramRejected("getUpdates: HTTP 401")])
def test_rejected_token_stops_bot(error: Exception) -> None:
    api, telegram = Api(), FakeTelegram([error])
    if isinstance(error, ServiceRejected):
        api.claim_error = error

    with pytest.raises(type(error)):
        bot(api, telegram).run()


def test_unexpected_error_is_logged_and_bot_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    api, telegram = Api(), FakeTelegram([RuntimeError("сломалось"), [say("/help", 3)]])
    waits: list[float] = []
    worker = Bot(api, telegram, wait=lambda seconds: waits.append(seconds) or False)
    answered = telegram.send

    def send_then_stop(chat_id: int, text: str) -> None:
        answered(chat_id, text)
        worker.stop()

    telegram.send = send_then_stop  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR):
        worker.run()

    assert any(record.getMessage() == "Сбой обхода бота" and record.exc_info for record in caplog.records)
    assert waits == [TELEGRAM_RETRY_S] and telegram.messages == [(CHAT, commands.HELP)]
