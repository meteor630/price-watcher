"""Ручная проверка цены по просьбе пользователя: кнопка «Проверить цену» у товара и «Перепроверить» в проверке цены.

Общие правила (одно место для обоих сценариев):
- задание ставится с повышенным приоритетом (как при добавлении ссылки) и обгоняет плановый прогон, даже идущий:
  если у ссылки уже стоит плановое задание в очереди, его приоритет поднимается, дубль не создаётся;
- лимит частоты — по журналу действий пользователя: не больше 20 ручных проверок в час на пользователя
  (нажатие у товара — одна проверка, сколько бы ссылок у него ни было); сверх — RATE_LIMITED с временем до следующей;
- каждое нажатие — событие в журнале действий;
- worker давно не обращался к серверу (дом выключен) — задание всё равно ставится и выполнится, когда worker вернётся;
  вызывающий узнаёт об этом из итога, чтобы показать понятный текст.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.application.ports import AuditEvent, UnitOfWork
from pricewatch.application.worker_protocol import LEASE
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

MANUAL_CHECK_ACTION = "price_recheck"
MANUAL_CHECKS_PER_HOUR = 20
MANUAL_CHECK_WINDOW = timedelta(hours=1)
# Worker молчит дольше аренды задания — считаем, что домашний узел не на связи.
WORKER_SILENT_AFTER = LEASE


@dataclass(frozen=True, slots=True)
class QueuedChecks:
    """Итог постановки: новые задания и уже стоявшие в очереди (их приоритет поднят), на связи ли worker."""

    new_task_ids: tuple[int, ...]
    already_queued: tuple[int, ...]
    worker_online: bool


def ensure_manual_check_allowed(uow: UnitOfWork, user_id: int, now: datetime) -> None:
    """Внутри открытой транзакции: лимит ручных проверок пользователя не исчерпан, иначе RATE_LIMITED."""
    recent = uow.audit.recent(user_id, MANUAL_CHECK_ACTION, now - MANUAL_CHECK_WINDOW)
    if len(recent) >= MANUAL_CHECKS_PER_HOUR:
        oldest = recent[len(recent) - MANUAL_CHECKS_PER_HOUR]
        raise PriceWatcherError(
            ErrorCode.RATE_LIMITED, {"retry_after_seconds": max(1, math.ceil((oldest + MANUAL_CHECK_WINDOW - now).total_seconds()))}
        )


def queue_manual_checks(
    uow: UnitOfWork, user_id: int, tracked_link_ids: list[int], entity: tuple[str, int], details: dict[str, object], now: datetime,
) -> QueuedChecks:
    """Внутри открытой транзакции (лимит уже проверен): задания для ссылок и одна запись в журнале на нажатие."""
    new: list[int] = []
    queued: list[int] = []
    for link_id in tracked_link_ids:
        active = uow.queue.active_task(link_id)
        if active is None:
            new.append(uow.queue.enqueue_on_add(link_id, now))
        else:
            uow.queue.prioritize(active)
            queued.append(active)
    entity_type, entity_id = entity
    uow.audit.add(AuditEvent(user_id, MANUAL_CHECK_ACTION, entity_type, entity_id, None, {**details, "task_ids": new, "queued_task_ids": queued}, now))
    seen = uow.workers.last_seen()
    return QueuedChecks(tuple(new), tuple(queued), seen is not None and now - seen < WORKER_SILENT_AFTER)


def check_item(uow: UnitOfWork, user: User, item_id: int, now: datetime) -> QueuedChecks:
    """«Проверить цену» у товара: все его действующие ссылки в включённых магазинах. Чужой или удалённый товар — NOT_FOUND;
    ссылок для проверки нет — VALIDATION_ERROR (поле links)."""
    with uow:
        uow.items.get(user.id, item_id)
        links = [
            binding for binding in uow.price_data.bindings(user.id)
            if binding.item_id == item_id and binding.valid_from <= now and (binding.valid_to is None or binding.valid_to > now)
        ]
        stores = uow.price_data.stores(sorted({binding.store_id for binding in links}))
        link_ids = sorted({binding.tracked_link_id for binding in links if stores[binding.store_id].enabled})
        if not link_ids:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"links": "no_active_links"}})
        ensure_manual_check_allowed(uow, user.id, now)
        result = queue_manual_checks(uow, user.id, link_ids, ("item", item_id), {}, now)
        uow.commit()
    return result


@dataclass(frozen=True, slots=True)
class ItemCheckState:
    """Состояние проверки товара для кнопки: идёт ли проверка какой-то его ссылки, на связи ли worker,
    когда была последняя проверка и лучшая найденная цена в копейках (None — цены нет)."""

    checking: bool
    worker_online: bool
    last_checked_at: datetime | None
    best_price_minor: int | None


def item_check_state(uow: UnitOfWork, user: User, item_id: int, now: datetime) -> ItemCheckState:
    """Чужой или удалённый товар — NOT_FOUND."""
    with uow:
        uow.items.get(user.id, item_id)
        states = uow.price_data.link_states(user.id, item_id)
        seen = uow.workers.last_seen()
    checked = [state.last_checked_at for state in states if state.last_checked_at is not None]
    prices: list[int] = []
    for state in states:
        shown = state.last_card_price_minor or state.last_price_minor
        if state.last_status == "ok" and state.last_availability != "out_of_stock" and shown is not None:
            prices.append(shown)
    return ItemCheckState(
        any(state.checking and state.store_enabled for state in states),
        seen is not None and now - seen < WORKER_SILENT_AFTER,
        max(checked) if checked else None,
        min(prices) if prices else None,
    )
