"""Повтор изменяющих запросов приложения (DEVELOPMENT_ARCHITECTURE.md, разделы 5 и 9).

- Ключ занимается до выполнения запроса. Повтор с тем же ключом и тем же телом получает
  сохранённый ответ, изменение второй раз не делается. Тот же ключ с другим телом — IDEMPOTENCY_KEY_REUSED.
- Пока первый запрос выполняется, повтор получает тот же отказ с пометкой in_progress.
- Ошибка запроса ключ освобождает: исправленный запрос можно отправить с тем же ключом.
- Ответ хранится 24 часа; ключ, оставшийся без ответа (процесс оборвался), освобождается через 2 минуты.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

KEY_LIFETIME = timedelta(hours=24)
ABANDONED_AFTER = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class Replay:
    status: int
    body: object


def begin(uow: UnitOfWork, user_id: int, key: str, request_hash: str, now: datetime) -> Replay | None:
    """`None` — ключ занят этим запросом, выполняйте; иначе сохранённый ответ прежнего такого же запроса."""
    with uow:
        uow.idempotency.drop_stale(user_id, key, now - KEY_LIFETIME, now - ABANDONED_AFTER)
        claimed = uow.idempotency.claim(user_id, key, request_hash, now)
        stored = None if claimed else uow.idempotency.get(user_id, key)
        uow.commit()
    if stored is None:
        return None
    if stored.request_hash != request_hash:
        raise PriceWatcherError(ErrorCode.IDEMPOTENCY_KEY_REUSED)
    if stored.status is None:
        raise PriceWatcherError(ErrorCode.IDEMPOTENCY_KEY_REUSED, {"in_progress": True})
    return Replay(stored.status, stored.body)


def finish(uow: UnitOfWork, user_id: int, key: str, status: int, body: object) -> None:
    with uow:
        uow.idempotency.complete(user_id, key, status, body)
        uow.commit()


def abandon(uow: UnitOfWork, user_id: int, key: str) -> None:
    with uow:
        uow.idempotency.release(user_id, key)
        uow.commit()


def purge_expired(uow: UnitOfWork, now: datetime) -> int:
    with uow:
        removed = uow.idempotency.purge(now - KEY_LIFETIME)
        uow.commit()
    return removed
