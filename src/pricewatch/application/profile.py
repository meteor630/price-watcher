"""Профиль пользователя на сайте (SITE_DESIGN_SPEC.md, раздел 14; DEVELOPMENT_ARCHITECTURE.md, раздел 4).

- Подключение приложения — код из application.app_auth.issue_pairing_code (8 символов, 5 минут).
- Устройства: список неотозванных, «Отозвать» закрывает доступ устройства и его токенов сразу.
- Смена пароля: текущий пароль проверяется с тем же лимитом попыток, что вход (неверный — попытка в счёт);
  новый — по правилам регистрации. После смены отзываются все остальные сессии и все устройства,
  текущая сессия остаётся. Действия пишутся в журнал.
"""

from datetime import datetime

from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.ports import AuditEvent, DeviceOverview, UnitOfWork
from pricewatch.application.web_auth import LOGIN_FAILURE, SessionUser, ensure_login_not_limited
from pricewatch.domain.accounts import User, check_password, login_key
from pricewatch.domain.errors import ErrorCode, PriceWatcherError


def devices(uow: UnitOfWork, user: User) -> list[DeviceOverview]:
    with uow:
        return uow.devices.list_active(user.id)


def revoke_device(uow: UnitOfWork, user: User, device_id: int, now: datetime) -> None:
    with uow:
        if not uow.devices.revoke_owned(user.id, device_id, now):
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        uow.audit.add(AuditEvent(user.id, "device_revoked", "device", device_id, None, None, now))
        uow.commit()


def change_password(
    uow: UnitOfWork, hasher: PasswordHasher, session: SessionUser, *, current: str, new: str, repeat: str, ip: str | None, now: datetime
) -> None:
    """VALIDATION_ERROR по полям: current_password (wrong), password (правила), password_repeat (mismatch)."""
    user = session.user
    subject = login_key(user.login)
    with uow:
        ensure_login_not_limited(uow, subject, ip, now)
        credentials = uow.users.find_credentials(subject)
        if credentials is None or credentials.user.id != user.id:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        if not hasher.verify(credentials.password_hash, current):
            uow.auth_failures.record(LOGIN_FAILURE, subject, ip, now)
            uow.commit()
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"current_password": "wrong"}})
        password = check_password(new, user.login)
        if new != repeat:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"password_repeat": "mismatch"}})
        uow.users.set_password_hash(user.id, hasher.hash(password))
        sessions = uow.sessions.revoke_others(user.id, session.session.id, now)
        revoked_devices = uow.devices.revoke_all(user.id, now)
        uow.auth_failures.clear_subject(LOGIN_FAILURE, subject)
        uow.audit.add(AuditEvent(user.id, "password_changed", "user", user.id, None, {"sessions_revoked": sessions, "devices_revoked": revoked_devices}, now))
        uow.commit()
