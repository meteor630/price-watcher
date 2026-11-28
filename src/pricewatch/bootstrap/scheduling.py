"""Запуск планировщика: python -m pricewatch.bootstrap scheduler [--backups].

Настройки и проверка базы — как у сервера; коды выхода те же: 2 — конфигурация, 3 — база или схема.
--backups — в том же процессе служба ежедневной резервной копии (настройки backup.env, образ backup с pg_dump и age):
на сервере копии делает планировщик, отдельного контейнера нет.
Сигнал остановки (Ctrl+C, SIGTERM) завершает процесс после текущего обхода; служба копий останавливается вместе с ним.
"""

import logging
import signal
import threading
from datetime import datetime, time
from pathlib import Path

from sqlalchemy import Engine

from pricewatch.application.backups import BackupAlerts, watch_backups
from pricewatch.application.client_errors import purge_client_errors
from pricewatch.application.idempotency import purge_expired
from pricewatch.application.ports import PurgedCounts
from pricewatch.application.retention import delete_blocked_users_data, purge_deleted
from pricewatch.application.runs import ExpiredLeases, ScheduledStart, expire_leases, schedule_times, start_scheduled_run
from pricewatch.application.summaries import send_run_summaries
from pricewatch.application.watchdog import WatchReport, watch
from pricewatch.backup.service import BackupService
from pricewatch.bootstrap.backups import backup_service
from pricewatch.bootstrap.server import EXIT_CONFIG, EXIT_DATABASE, check_database
from pricewatch.config.settings import BackupSettings, ConfigError, load_backup_settings, load_settings
from pricewatch.observability.json_log import configure_logging
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork
from pricewatch.scheduler.loop import Scheduler

logger = logging.getLogger(__name__)


class SqlJobs:
    """Сценарии запусков на настоящей базе: каждый вызов — своя транзакция."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def schedule_times(self) -> tuple[time, ...]:
        return schedule_times(SqlUnitOfWork(self._engine))

    def start_scheduled_run(self, slot_key: str, now: datetime) -> ScheduledStart:
        return start_scheduled_run(SqlUnitOfWork(self._engine), slot_key, now)

    def expire_leases(self, now: datetime) -> ExpiredLeases:
        return expire_leases(SqlUnitOfWork(self._engine), now)

    def delete_blocked_users_data(self, now: datetime) -> int:
        return delete_blocked_users_data(SqlUnitOfWork(self._engine), now)

    def purge_deleted(self, now: datetime) -> PurgedCounts:
        return purge_deleted(SqlUnitOfWork(self._engine), now)

    def purge_idempotency_keys(self, now: datetime) -> int:
        return purge_expired(SqlUnitOfWork(self._engine), now)

    def purge_client_errors(self, now: datetime) -> int:
        return purge_client_errors(SqlUnitOfWork(self._engine), now)

    def watch(self, now: datetime) -> WatchReport:
        return watch(SqlUnitOfWork(self._engine), now)

    def send_run_summaries(self, now: datetime) -> int:
        return send_run_summaries(SqlUnitOfWork(self._engine), now)

    def watch_backups(self, now: datetime) -> BackupAlerts:
        return watch_backups(SqlUnitOfWork(self._engine), now)


def run_scheduler(env_file: Path = Path(".env"), *, backups: bool = False, backup_env: Path = Path("backup.env")) -> int:
    try:
        settings = load_settings(env_file)
        backup: BackupSettings | None = load_backup_settings(backup_env) if backups else None
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        if not check_database(engine):
            return EXIT_DATABASE
        scheduler = Scheduler(SqlJobs(engine))
        copies: BackupService | None = None if backup is None else backup_service(settings, backup, engine)
        copies_thread = None if copies is None else threading.Thread(target=copies.run, name="backups", daemon=True)

        def stop(*_: object) -> None:
            scheduler.stop()
            if copies is not None:
                copies.stop()

        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), stop)
        if copies_thread is not None:
            copies_thread.start()
            logger.info("Служба резервных копий запущена")
        logger.info("Планировщик запущен")
        scheduler.run()
        if copies is not None and copies_thread is not None:
            copies.stop()
            copies_thread.join()
    finally:
        engine.dispose()
    logger.info("Планировщик остановлен")
    return 0
