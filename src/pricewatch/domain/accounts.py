"""Пользователи: роли, правила логина и пароля (DEVELOPMENT_ARCHITECTURE.md, раздел 4).

Логин — 3–64 символа: латиница, цифры, точка, дефис, подчёркивание; сравнивается
без учёта регистра. Пароль — 10–128 символов после нормализации Unicode NFKC
и не совпадает с логином; правил состава нет (NIST SP 800-63B).
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import clean_name

LOGIN_PATTERN = re.compile(r"[A-Za-z0-9._-]{3,64}")
PASSWORD_MIN = 10
PASSWORD_MAX = 128
DISPLAY_NAME_MAX = 100


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
