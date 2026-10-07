"""Пользователи у администратора (SITE_DESIGN_SPEC.md, раздел 8 «Пользователи»; DEVELOPMENT_ARCHITECTURE.md, разделы 4 и 5).

- Блокировка: войти нельзя сразу — все сессии и устройства отзываются; товары перестают проверяться;
  через 7 суток данные мягко удаляются (application.retention).
- Разблокировка: до удаления данные просто остаются; после — возвращается всё, что удалила блокировка,
  пока очистка (30 суток) не стёрла его. Удалённое самим пользователем не возвращается.
- «Снять права администратора» вместо блокировки — для администратора (решение владельца 07.10.2026).
- Нельзя заблокировать или лишить прав себя и последнего действующего администратора.
- Приглашения (создаёт application.accounts.create_invite): ссылка показывается один раз; неиспользованные можно выбрать и удалить.
Каждое действие пишется в журнал.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.ports import AuditEvent, InviteOverview, UnitOfWork, UserOverview
from pricewatch.application.retention import BLOCKED_DATA_DELETED
from pricewatch.application.runs import require_admin
from pricewatch.domain.accounts import Role, User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.shopping import PURGE_AFTER


@dataclass(frozen=True, slots=True)
class UsersPage:
    users: list[UserOverview]
    invites: list[InviteOverview]


@dataclass(frozen=True, slots=True)
class Unblocked:
    """Сколько записей вернулось; `too_late` — данные уже стёрты очисткой."""

    restored: int
    too_late: bool


def users_page(uow: UnitOfWork, actor: User) -> UsersPage:
    with uow:
        require_admin(uow, actor.id)
        return UsersPage(uow.users.overview(), uow.invites.unused())


def block_user(uow: UnitOfWork, actor: User, user_id: int, now: datetime) -> None:
    with uow:
        require_admin(uow, actor.id)
        target = _other(uow, actor, user_id)
        if target.disabled:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"user": "already_blocked"}})
        _keep_an_admin(uow, target)
        uow.users.set_disabled(user_id, now)
        sessions = uow.sessions.revoke_all(user_id, now)
        devices = uow.devices.revoke_all(user_id, now)
        uow.audit.add(AuditEvent(actor.id, "user_blocked", "user", user_id, None, {"sessions_revoked": sessions, "devices_revoked": devices}, now))
        uow.commit()


def unblock_user(uow: UnitOfWork, actor: User, user_id: int, now: datetime) -> Unblocked:
    with uow:
        require_admin(uow, actor.id)
        target = next((user for user in uow.users.overview() if user.user_id == user_id), None)
        if target is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        if target.disabled_at is None:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"user": "not_blocked"}})
        deleted = uow.audit.last(BLOCKED_DATA_DELETED, "user", user_id)
        restored, too_late = 0, False
        # Удаление этой блокировки (а не прошлой) — не раньше её начала.
        if deleted is not None and deleted.at >= target.disabled_at:
            restored = uow.personal_data.restore_deleted_at(user_id, deleted.at)
            too_late = restored == 0 and now - deleted.at >= PURGE_AFTER
        uow.users.set_disabled(user_id, None)
        uow.audit.add(AuditEvent(actor.id, "user_unblocked", "user", user_id, None, {"restored": restored}, now))
        uow.commit()
    return Unblocked(restored, too_late)


def demote_admin(uow: UnitOfWork, actor: User, user_id: int, now: datetime) -> None:
    with uow:
        require_admin(uow, actor.id)
        target = _other(uow, actor, user_id)
        if not target.is_admin:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"user": "not_admin"}})
        _keep_an_admin(uow, target)
        uow.users.set_role(user_id, Role.USER)
        uow.audit.add(AuditEvent(actor.id, "admin_demoted", "user", user_id, {"role": Role.ADMIN.value}, {"role": Role.USER.value}, now))
        uow.commit()


def delete_invites(uow: UnitOfWork, actor: User, invite_ids: Sequence[int], now: datetime) -> list[int]:
    if not invite_ids:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"invites": "none_selected"}})
    with uow:
        require_admin(uow, actor.id)
        deleted = uow.invites.delete_unused(invite_ids)
        for invite_id in deleted:
            uow.audit.add(AuditEvent(actor.id, "invite_deleted", "invite", invite_id, None, None, now))
        uow.commit()
    return deleted


def _other(uow: UnitOfWork, actor: User, user_id: int) -> User:
    if user_id == actor.id:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"user": "self"}})
    return uow.users.get(user_id)


def _keep_an_admin(uow: UnitOfWork, target: User) -> None:
    admins = uow.users.lock_admins()
    if target.id in admins and len(admins) == 1:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"user": "last_admin"}})
