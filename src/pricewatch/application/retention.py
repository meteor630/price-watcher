"""Удаление данных заблокированных пользователей и окончательная очистка удалённого
(DEVELOPMENT_ARCHITECTURE.md, раздел 5, «Удаление и отмена»).

Через 7 суток после блокировки все товары (с покупками), группы и категории пользователя мягко
удаляются; окончательно их сотрёт общая очистка через 30 дней. Учётная запись остаётся
заблокированной. Общие ссылки каталога и история цен не трогаются. Разблокировка до срока снимает
отметку блокировки — удалять нечего; повторная блокировка начинает отсчёт заново. Разблокировка после
удаления, пока очистка не стёрла данные, возвращает всё, что удалила блокировка (application.users_admin).

Мягко удалённые категории, группы и товары стираются окончательно через 30 дней — вместе с тем,
что в них лежит, и с привязками ссылок. Каталог ссылок и история цен общие и остаются.
"""

from datetime import datetime

from pricewatch.application.ports import AuditEvent, PurgedCounts, UnitOfWork
from pricewatch.domain.accounts import BLOCKED_DATA_KEPT
from pricewatch.domain.shopping import PURGE_AFTER

BLOCKED_DATA_DELETED = "blocked_user_data_deleted"


def delete_blocked_users_data(uow: UnitOfWork, now: datetime) -> int:
    """Число пользователей, чьи данные удалены в этот раз."""
    with uow:
        owners = uow.personal_data.blocked_owners(now - BLOCKED_DATA_KEPT)
        for user_id in owners:
            uow.personal_data.soft_delete_all(user_id, now)
            # Момент удаления — по нему «Разблокировать» вернёт ровно то, что удалила блокировка.
            uow.audit.add(AuditEvent(None, BLOCKED_DATA_DELETED, "user", user_id, None, {"deleted_at": now.isoformat()}, now))
        uow.commit()
    return len(owners)


def purge_deleted(uow: UnitOfWork, now: datetime) -> PurgedCounts:
    with uow:
        purged = uow.personal_data.purge_deleted(now - PURGE_AFTER)
        uow.commit()
    return purged
