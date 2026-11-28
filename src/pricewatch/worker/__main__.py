"""Запуск worker: python -m pricewatch.worker.

Настройки — переменные PRICEWATCH_WORKER_* и файл worker.env. Сигнал остановки (Ctrl+C, SIGTERM)
дожидается конца текущей проверки. Коды выхода: 0 — остановлен, 2 — конфигурация (в том числе нет или испорчен
список доменов allowlist.txt), 4 — сервер не принял токен, 5 — исчерпан суточный лимит заданий.
"""

import logging
import signal
import sys
from datetime import UTC, datetime

from pricewatch.config.settings import ConfigError, load_worker_settings
from pricewatch.observability.json_log import configure_logging, use_utf8_streams
from pricewatch.worker.allowlist import AllowlistUnusable, EgressGate, read_domains
from pricewatch.worker.client import ApiClient, ServerAnswerRejected, WorkerRejected, http_client
from pricewatch.worker.images import ImageLoader
from pricewatch.worker.limits import DailyLimitReached, DailyTaskLimit
from pricewatch.worker.parsing import Parsers
from pricewatch.worker.runner import Runner

EXIT_CONFIG = 2
EXIT_REJECTED = 4
EXIT_DAILY_LIMIT = 5
EXIT_SERVER_ANSWER = 6
DAILY_COUNTER = "daily-tasks.json"

logger = logging.getLogger("pricewatch.worker")


def main() -> int:
    try:
        settings = load_worker_settings()
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging(settings.log_level)
    try:
        allowed = read_domains(settings.allowlist)
    except AllowlistUnusable as error:
        logger.error("Список доменов домашнего узла не читается — worker не берёт заданий", extra={"reason": str(error)})
        return EXIT_CONFIG
    gate = EgressGate(settings.allowlist)
    limit = DailyTaskLimit(settings.daily_task_limit, settings.profiles_dir / DAILY_COUNTER, lambda: datetime.now(UTC))
    parsers = Parsers(settings.profiles_dir)
    images = ImageLoader()
    with http_client(settings.api_url) as client:
        runner = Runner(
            ApiClient(client, settings.token.get_secret_value()), parsers, images, gate=gate, limit=limit, poll_seconds=settings.poll_seconds,
        )
        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: runner.stop())
        logger.info("Worker запущен", extra={"api": settings.api_url, "allowed_domains": len(allowed), "daily_task_limit": settings.daily_task_limit})
        try:
            runner.run()
        except WorkerRejected:
            logger.error("Сервер не принял токен worker: проверьте PRICEWATCH_WORKER_TOKEN или выпустите новый командой rotate-worker-token")
            return EXIT_REJECTED
        except DailyLimitReached:
            return EXIT_DAILY_LIMIT
        except AllowlistUnusable as error:
            logger.error("Список доменов домашнего узла не читается — worker останавливается", extra={"reason": str(error)})
            return EXIT_CONFIG
        except ServerAnswerRejected as error:
            logger.error("Ответ сервера не по схеме — worker останавливается: проверьте сервер", extra={"reason": str(error)})
            return EXIT_SERVER_ANSWER
        finally:
            parsers.close()
            images.close()
    logger.info("Worker остановлен")
    return 0


use_utf8_streams()
sys.exit(main())
