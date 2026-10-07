"""Магазины у администратора (SITE_DESIGN_SPEC.md, раздел 8 «Магазины»).

Магазины создаются сами, когда пользователь добавляет ссылку с нового сайта (решение владельца 06.10.2026).
Администратор меняет название и цвет, включает и отключает магазин и удаляет неиспользуемый:
- отключённый магазин не проверяется, у пользователей остаётся последняя цена с пометкой «магазин
  отключён» вне итога, новые ссылки на него не добавляются; после включения всё возвращается;
- удалить можно магазин без ссылок каталога, привязок и покупок.
Изменение действует сразу: последний сохранивший администратор прав (у магазина нет версии записи).
Каждое действие пишется в журнал.
"""

import re
from datetime import datetime

from pricewatch.application.ports import AuditEvent, StoreOverview, UnitOfWork
from pricewatch.application.runs import require_admin
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import clean_name

STORE_NAME_MAX = 100
COLORS = tuple(f"store-{index}" for index in range(1, 9))
COLOR_FORMAT = re.compile(r"store-[1-8]")


def stores_overview(uow: UnitOfWork, user: User) -> list[StoreOverview]:
    with uow:
        require_admin(uow, user.id)
        return uow.stores.overview()


def change_store(uow: UnitOfWork, user: User, store_id: int, *, name: str, color: str, now: datetime) -> StoreOverview:
    display_name = clean_name(name, field="name", max_length=STORE_NAME_MAX)
    if COLOR_FORMAT.fullmatch(color) is None:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"color": "invalid"}})
    with uow:
        require_admin(uow, user.id)
        before = _find(uow, store_id)
        after = uow.stores.update(store_id, display_name=display_name, color=color)
        uow.audit.add(AuditEvent(user.id, "store_changed", "store", store_id, _state(before), _state(after), now))
        uow.commit()
    return after


def set_store_enabled(uow: UnitOfWork, user: User, store_id: int, enabled: bool, now: datetime) -> StoreOverview:
    with uow:
        require_admin(uow, user.id)
        before = _find(uow, store_id)
        after = uow.stores.set_enabled(store_id, enabled)
        uow.audit.add(AuditEvent(user.id, "store_enabled" if enabled else "store_disabled", "store", store_id, _state(before), _state(after), now))
        uow.commit()
    return after


def delete_store(uow: UnitOfWork, user: User, store_id: int, now: datetime) -> None:
    with uow:
        require_admin(uow, user.id)
        removed = uow.stores.delete_unused(store_id)
        uow.audit.add(AuditEvent(user.id, "store_deleted", "store", store_id, _state(removed), None, now))
        uow.commit()


def _find(uow: UnitOfWork, store_id: int) -> StoreOverview:
    found = next((store for store in uow.stores.overview() if store.store_id == store_id), None)
    if found is None:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return found


def _state(store: StoreOverview) -> dict[str, object]:
    return {"name": store.display_name, "color": store.color_token, "enabled": store.enabled, "host": store.canonical_host}
