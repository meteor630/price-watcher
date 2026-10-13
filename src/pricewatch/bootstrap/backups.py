"""Служба резервных копий: python -m pricewatch.bootstrap backup [--once].

Настройки и проверка базы — как у сервера, плюс backup.env (PRICEWATCH_BACKUP_…); коды выхода те же: 2 — конфигурация,
3 — база или схема. --once — одна копия сейчас: 0 — удалась, 1 — нет. Без него — ежедневно в заданное время.
"""

import logging
import signal
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import make_url

from pricewatch.application.backups import make_backup
from pricewatch.backup.archive import AgeArchive, DatabaseAddress
from pricewatch.backup.service import BackupService
from pricewatch.bootstrap.server import EXIT_CONFIG, EXIT_DATABASE, check_database
from pricewatch.config.settings import ConfigError, load_backup_settings, load_settings
from pricewatch.observability.json_log import configure_logging
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

logger = logging.getLogger(__name__)
POSTGRES_PORT = 5432


def run_backups(once: bool, env_file: Path = Path(".env"), backup_env: Path = Path("backup.env")) -> int:
    try:
        settings = load_settings(env_file)
        backup = load_backup_settings(backup_env)
    except ConfigError as error:
        configure_logging("ERROR")
        logger.error(str(error))
        return EXIT_CONFIG
    configure_logging(settings.log_level)
    url = make_url(settings.database_url.get_secret_value())
    database = DatabaseAddress(url.host or "localhost", url.port or POSTGRES_PORT, url.username or "", url.database or "", url.password or "")
    archive = AgeArchive(backup.dir, settings.media_dir, backup.recipient, database)
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        if not check_database(engine):
            return EXIT_DATABASE
        service = BackupService(lambda: make_backup(lambda: SqlUnitOfWork(engine), archive, lambda: datetime.now(UTC)), backup.time)
        if once:
            return 0 if service.run_once().status == "ok" else 1
        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: service.stop())
        logger.info("Служба резервных копий запущена")
        service.run()
    finally:
        engine.dispose()
    logger.info("Служба резервных копий остановлена")
    return 0
