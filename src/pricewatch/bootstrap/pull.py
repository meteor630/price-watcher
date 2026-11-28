"""Домашний приёмник копий: python -m pricewatch.bootstrap pull-backups [--once].

Настройки — pull.env (PRICEWATCH_PULL_…). Сразу при старте (ПК мог быть выключен) и затем ежедневно в заданное время
по Москве забирает с сервера копии, которых дома нет (backup.pull). Коды выхода: 2 — конфигурация;
--once — 0, если список получен и все новые копии сошлись, иначе 1. Сбой одного забора — в журнал, служба ждёт следующего.
"""

import logging
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx2

from pricewatch.backup.pull import BackupPuller, PullError
from pricewatch.bootstrap.server import EXIT_CONFIG
from pricewatch.config.settings import ConfigError, load_pull_settings
from pricewatch.domain.schedule import next_slot_at
from pricewatch.observability.json_log import configure_logging

logger = logging.getLogger(__name__)
# Копия идёт потоком: долгое чтение допустимо, соединение — нет.
TIMEOUT = httpx2.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


def pull_once(puller: BackupPuller) -> bool:
    try:
        report = puller.pull()
    except PullError as error:
        logger.error("Копии не забраны", extra={"reason": str(error)})
        return False
    logger.info("Забор копий завершён", extra={"received": len(report.received), "rejected": len(report.rejected), "removed": len(report.removed)})
    return not report.rejected


def run_pull(once: bool, env_file: Path = Path("pull.env")) -> int:
    try:
        settings = load_pull_settings(env_file)
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging("INFO")
    with httpx2.Client(base_url=settings.api_url, timeout=TIMEOUT, trust_env=False, follow_redirects=False) as client:
        puller = BackupPuller(client, settings.token.get_secret_value(), settings.dir, settings.max_bytes, lambda: datetime.now(UTC))
        if once:
            return 0 if pull_once(puller) else 1
        stopping = threading.Event()
        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: stopping.set())
        logger.info("Приёмник копий запущен")
        while not stopping.is_set():
            pull_once(puller)
            now = datetime.now(UTC)
            due = next_slot_at((settings.time,), now)
            logger.info("Следующий забор копий", extra={"at": due.isoformat()})
            stopping.wait((due - now).total_seconds())
    logger.info("Приёмник копий остановлен")
    return 0
