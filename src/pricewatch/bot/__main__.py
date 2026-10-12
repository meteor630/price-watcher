"""Запуск Telegram-бота: python -m pricewatch.bot.

Настройки — переменные PRICEWATCH_BOT_* и файл bot.env. Сигнал остановки (Ctrl+C, SIGTERM) завершает бота
после текущего обхода. Коды выхода: 0 — остановлен, 2 — конфигурация, 4 — сервер не принял токен сервиса
или Telegram не принял токен бота.
"""

import logging
import signal
import sys

from pricewatch.bot import api, telegram
from pricewatch.bot.api import InternalApi, ServiceRejected
from pricewatch.bot.runner import Bot
from pricewatch.bot.telegram import TelegramClient, TelegramRejected
from pricewatch.config.settings import ConfigError, load_bot_settings
from pricewatch.observability.json_log import configure_logging, use_utf8_streams

EXIT_CONFIG = 2
EXIT_REJECTED = 4

logger = logging.getLogger("pricewatch.bot")


def main() -> int:
    try:
        settings = load_bot_settings()
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging(settings.log_level)
    with api.http_client(settings.api_url) as server, telegram.http_client() as telegram_http:
        bot = Bot(InternalApi(server, settings.service_token.get_secret_value()), TelegramClient(telegram_http, settings.telegram_token.get_secret_value()))
        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: bot.stop())
        logger.info("Бот запущен", extra={"api": settings.api_url})
        try:
            bot.run()
        except ServiceRejected:
            logger.error("Сервер не принял токен сервиса: проверьте PRICEWATCH_BOT_SERVICE_TOKEN или выпустите новый командой rotate-service-token")
            return EXIT_REJECTED
        except TelegramRejected:
            logger.error("Telegram не принял токен бота: проверьте PRICEWATCH_BOT_TELEGRAM_TOKEN (выдаёт @BotFather)")
            return EXIT_REJECTED
    logger.info("Бот остановлен")
    return 0


use_utf8_streams()
sys.exit(main())
