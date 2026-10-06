"""Добавление ссылки на магазин к товару (DEVELOPMENT_ARCHITECTURE.md, разделы 7 и 8).

1. Адрес приводится к одному виду, магазин определяется по нему (LinkResolver — сборщик цен).
2. Магазина ещё нет — он создаётся сам, без подтверждения администратором (решение 06.10.2026);
   магазин, отключённый администратором, — STORE_NOT_ALLOWED.
3. Страница попадает в общий каталог один раз, сколько бы пользователей её ни добавили.
4. Ссылка привязывается к товару. У товара одна действующая ссылка на магазин: другая ссылка того же
   магазина заменяет прежнюю, история цены продолжается. Та же ссылка повторно — ничего не меняет.
5. Цене ссылки меньше суток — она видна сразу, проверка не нужна. Иначе ставится срочное задание
   на одну ссылку (on_add); активное задание на неё уже есть — новое не создаётся.

Лимит — 20 новых ссылок в час на пользователя.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.application.ports import LinkResolver, UnitOfWork
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

NEW_LINKS_PER_HOUR = 20
LINKS_WINDOW = timedelta(hours=1)
FRESH_PRICE = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class AddedLink:
    item_link_id: int
    tracked_link_id: int
    store_id: int
    # Номер срочного задания; None — свежая цена уже есть, проверка не нужна.
    task_id: int | None


def add_link(
    uow: UnitOfWork, resolver: LinkResolver, user_id: int, item_id: int, raw_url: str, region_code: str, now: datetime
) -> AddedLink:
    target = resolver.resolve(raw_url)
    with uow:
        uow.item_links.lock_item(user_id, item_id)
        store = uow.stores.get_or_create(target, now)
        if not store.enabled:
            raise PriceWatcherError(ErrorCode.STORE_NOT_ALLOWED)
        tracked_link_id = uow.tracked_links.get_or_create(store.id, target.normalized_url, region_code, now)
        current = uow.item_links.active_for_store(item_id, store.id)
        if current is not None and current.tracked_link_id == tracked_link_id:
            binding = current
        else:
            _check_rate(uow, user_id, now)
            if current is not None:
                uow.item_links.close(current.id, now)
            binding = uow.item_links.bind(user_id, item_id, tracked_link_id, store.id, now)
        task_id = None
        if not uow.observations.has_confirmed_success_since(tracked_link_id, now - FRESH_PRICE):
            task_id = uow.queue.enqueue_on_add(tracked_link_id, now)
        uow.commit()
    return AddedLink(binding.id, tracked_link_id, store.id, task_id)


def _check_rate(uow: UnitOfWork, user_id: int, now: datetime) -> None:
    recent = uow.item_links.added_since(user_id, now - LINKS_WINDOW)
    if len(recent) >= NEW_LINKS_PER_HOUR:
        # Освободится, когда самая ранняя из последних 20 выйдет из часового окна.
        oldest = recent[len(recent) - NEW_LINKS_PER_HOUR]
        retry_after = max(1, math.ceil((oldest + LINKS_WINDOW - now).total_seconds()))
        raise PriceWatcherError(ErrorCode.RATE_LIMITED, {"retry_after_seconds": retry_after})
