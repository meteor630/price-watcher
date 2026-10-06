"""Пользователи: роли, правила логина и пароля (DEVELOPMENT_ARCHITECTURE.md, раздел 4).

Логин — 3–64 символа: латиница, цифры, точка, дефис, подчёркивание; сравнивается
без учёта регистра. Пароль — 10–128 символов после нормализации Unicode NFKC
и не совпадает с логином; правил состава нет (NIST SP 800-63B).
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import clean_name

LOGIN_PATTERN = re.compile(r"[A-Za-z0-9._-]{3,64}")
PASSWORD_MIN = 10
PASSWORD_MAX = 128
DISPLAY_NAME_MAX = 100
INVITE_LIFETIME = timedelta(days=7)
SESSION_LIFETIME = timedelta(days=30)
SESSION_TOUCH_INTERVAL = timedelta(hours=1)
FAILURE_WINDOW = timedelta(minutes=15)
LOGIN_FAILURES_PER_LOGIN = 5
LOGIN_FAILURES_PER_IP = 20
PAIRING_CODE_LIFETIME = timedelta(minutes=5)
PAIRING_FAILURES_PER_IP = 10
ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
REFRESH_TOKEN_LIFETIME = timedelta(days=60)
REFRESH_REPLAY_WINDOW = timedelta(seconds=60)
DEVICE_NAME_MAX = 80
# Данные заблокированного пользователя мягко удаляются через 7 суток после блокировки (решение 06.10.2026).
BLOCKED_DATA_KEPT = timedelta(days=7)


class Role(StrEnum):
    USER = "user"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class User:
    id: int
    login: str
    role: Role
    display_name: str
    disabled: bool

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


@dataclass(frozen=True, slots=True)
class Invite:
    id: int
    role: Role
    created_by: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WebSession:
    id: int
    user_id: int
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Device:
    """Устройство приложения; оно же семья refresh-токенов."""

    id: int
    user_id: int
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedInvite:
    """Приглашение и его код; код показывается один раз и нигде не хранится."""

    invite: Invite
    code: str


def retry_after_seconds(oldest_failure: datetime, now: datetime) -> int:
    """Через сколько целых секунд окно неудач освободится от самой ранней из них; не меньше 1."""
    return max(1, math.ceil((oldest_failure + FAILURE_WINDOW - now).total_seconds()))


def check_device_name(raw: str) -> str:
    return clean_name(raw, field="device_name", max_length=DEVICE_NAME_MAX)


def check_login(raw: str) -> str:
    login = raw.strip()
    if not LOGIN_PATTERN.fullmatch(login):
        raise _invalid("login", "format")
    return login


def login_key(login: str) -> str:
    """Ключ сравнения логинов — тот же, что у уникального индекса lower(login)."""
    return login.lower()


def normalize_password(password: str) -> str:
    """Одинаковый пароль с разных клавиатур даёт одну строку: составная «й» и готовая совпадут."""
    return unicodedata.normalize("NFKC", password)


def check_password(password: str, login: str) -> str:
    normalized = normalize_password(password)
    if len(normalized) < PASSWORD_MIN:
        raise _invalid("password", "too_short")
    if len(normalized) > PASSWORD_MAX:
        raise _invalid("password", "too_long")
    if normalized.casefold() == login.casefold():
        raise _invalid("password", "same_as_login")
    return normalized


def check_display_name(raw: str) -> str:
    return clean_name(raw, field="display_name", max_length=DISPLAY_NAME_MAX)


def _invalid(field: str, reason: str) -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {field: reason}})
