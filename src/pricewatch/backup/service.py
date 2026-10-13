"""Служба резервных копий: ежедневно в заданное время по Москве — копия (application.backups.make_backup).

- Ожидание — до ближайшего времени копии; сигнал остановки прерывает ожидание сразу.
- Время, пропущенное из-за выключенного узла, не догоняется при старте: следующая копия — в ближайшее время копии.
  О давности последней удачной копии предупреждает планировщик (больше 36 часов, срез 11.3.3).
- Сбой самой попытки (база недоступна) пишется в журнал с трассировкой; служба ждёт следующего времени.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, time

from pricewatch.application.ports import BackupOutcome
from pricewatch.domain.schedule import next_slot_at

logger = logging.getLogger(__name__)


class BackupService:
    def __init__(
        self,
        job: Callable[[], BackupOutcome],
        at: time,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._job = job
        self._at = at
        self._clock = clock
        self._stopping = threading.Event()
        # Возвращает True, если ожидание прервано остановкой.
        self._wait = wait or self._stopping.wait

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        while not self._stopping.is_set():
            now = self._clock()
            due = next_slot_at((self._at,), now)
            logger.info("Следующая резервная копия", extra={"at": due.isoformat()})
            if self._wait((due - now).total_seconds()):
                return
            try:
                self.run_once()
            except Exception:
                # Служба живёт дальше: следующая попытка — в следующее время копии.
                logger.exception("Сбой попытки резервной копии")

    def run_once(self) -> BackupOutcome:
        outcome = self._job()
        if outcome.status == "ok":
            logger.info("Резервная копия создана", extra={"file": outcome.file_name, "size_bytes": outcome.size_bytes})
        else:
            logger.error("Резервная копия не удалась", extra={"error_code": outcome.error_code, "file": outcome.file_name})
        return outcome
