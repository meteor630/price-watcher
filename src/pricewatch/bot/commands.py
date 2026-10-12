"""Команды бота в личном чате: /start <код> — привязать Telegram, /code — код для приложения, остальное — подсказка.

Ответ — простой текст. Сервер недоступен — пользователь узнаёт об этом, а не ждёт молча.
"""

import re
from typing import Protocol

from pricewatch.bot.api import ApiUnavailable, NotFound, PairingCode
from pricewatch.domain.days import MOSCOW

COMMANDS = [("code", "Код для подключения приложения"), ("help", "Как привязать Telegram")]
# Параметр ссылки t.me/<бот>?start=<код>: латиница, цифры, «_» и «-», до 64 символов (ограничение Telegram).
START_CODE = re.compile(r"[A-Za-z0-9_-]{1,64}")

HOW_TO_LINK = "На сайте откройте Настройки → Профиль и нажмите «Привязать Telegram»."
HELP = f"Это бот Price Watcher: присылает сводку после проверки цен и выдаёт код для приложения. {HOW_TO_LINK} Код для приложения — команда /code."
LINKED = "Готово: Telegram привязан к пользователю «{name}». Сюда будут приходить сводки после проверки цен. Код для приложения — команда /code."
BAD_LINK_CODE = f"Код не подошёл: он неверный, уже использован или истёк. Получите новый: {HOW_TO_LINK}"
NOT_LINKED = f"Этот Telegram не привязан к Price Watcher. {HOW_TO_LINK}"
PAIRING = "Код для приложения: {code}. Действует до {until} по Москве, один раз."
SERVER_DOWN = "Сервер Price Watcher сейчас не отвечает. Попробуйте через несколько минут."


class Api(Protocol):
    def link(self, code: str, chat_id: int) -> str: ...
    def pairing_code(self, chat_id: int) -> PairingCode: ...


def reply(api: Api, chat_id: int, text: str) -> str:
    command, _, argument = text.strip().partition(" ")
    # В группах команда приходит как /code@имя_бота; в личном чате так тоже можно написать.
    name = command.split("@", 1)[0].lower()
    try:
        if name == "/start" and START_CODE.fullmatch(argument.strip()):
            return _link(api, chat_id, argument.strip())
        if name == "/code":
            return _pairing(api, chat_id)
    except ApiUnavailable:
        return SERVER_DOWN
    return HELP


def _link(api: Api, chat_id: int, code: str) -> str:
    try:
        return LINKED.format(name=api.link(code, chat_id))
    except NotFound:
        return BAD_LINK_CODE


def _pairing(api: Api, chat_id: int) -> str:
    try:
        issued = api.pairing_code(chat_id)
    except NotFound:
        return NOT_LINKED
    # Группами по 4 символа, как на сайте: «HMRP YF5P» легче перепечатать.
    code = " ".join(issued.code[start:start + 4] for start in range(0, len(issued.code), 4))
    return PAIRING.format(code=code, until=f"{issued.expires_at.astimezone(MOSCOW):%H:%M}")
