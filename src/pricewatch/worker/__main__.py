"""Запуск worker: python -m pricewatch.worker.

Настройки — переменные PRICEWATCH_WORKER_* и файл worker.env. Сигнал остановки (Ctrl+C, SIGTERM)
дожидается конца текущей проверки. Коды выхода: 0 — остановлен, 2 — конфигурация, 4 — сервер не принял токен.
"""

import logging
import signal
import sys

from pricewatch.config.settings import ConfigError, load_worker_settings
from pricewatch.observability.json_log import configure_logging, use_utf8_streams
from pricewatch.worker.client import ApiClient, WorkerRejected, http_client
from pricewatch.worker.parsing import Parsers
from pricewatch.worker.runner import Runner

EXIT_CONFIG = 2
EXIT_REJECTED = 4

logger = logging.getLogger("pricewatch.worker")


def main() -> int:
    try:
        settings = load_worker_settings()
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging(settings.log_level)
    parsers = Parsers(settings.profiles_dir)
    with http_client(settings.api_url) as client:
        runner = Runner(ApiClient(client, settings.token.get_secret_value()), parsers, poll_seconds=settings.poll_seconds)
        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: runner.stop())
        logger.info("Worker запущен", extra={"api": settings.api_url})
        try:
            runner.run()
        except WorkerRejected:
            logger.error("Сервер не принял токен worker: проверьте PRICEWATCH_WORKER_TOKEN или выпустите новый командой rotate-worker-token")
            return EXIT_REJECTED
        finally:
            parsers.close()
    logger.info("Worker остановлен")
    return 0


use_utf8_streams()
sys.exit(main())
