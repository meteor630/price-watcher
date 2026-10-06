"""Удаление данных заблокированных пользователей (DEVELOPMENT_ARCHITECTURE.md, раздел 5, «Удаление и отмена»).

Через 7 суток после блокировки все товары (с покупками), группы и категории пользователя мягко
удаляются; окончательно их сотрёт общая очистка через 30 дней. Учётная запись остаётся
заблокированной. Общие ссылки каталога и история цен не трогаются. Разблокировка до срока снимает
отметку блокировки — удалять нечего; повторная блокировка начинает отсчёт заново.
"""

from datetime import datetime

from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.accounts import BLOCKED_DATA_KEPT


def delete_blocked_users_data(uow: UnitOfWork, now: datetime) -> int:
    """Число пользователей, чьи данные удалены в этот раз."""
    with uow:
        owners = uow.personal_data.blocked_owners(now - BLOCKED_DATA_KEPT)
        for user_id in owners:
            uow.personal_data.soft_delete_all(user_id, now)
        uow.commit()
    return len(owners)
