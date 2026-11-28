"""Случайные токены, коды связки и HMAC с серверным ключом.

В базе хранится только keyed_hash(...) — HMAC-SHA256 с серверным ключом и
назначением. Назначение (purpose) разводит одинаковые строки разного смысла:
токен сессии и refresh-токен с одним значением дадут разные хеши.

Следующая пара токенов выводится из предыдущего refresh-токена (derive_token):
повтор в окне 60 секунд пересчитывает ту же пару, ничего не храня в открытом виде
(DEVELOPMENT_ARCHITECTURE.md, раздел 4).
"""

import base64
import hashlib
import hmac
import secrets
from enum import StrEnum

TOKEN_BYTES = 32
PAIRING_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
PAIRING_CODE_LENGTH = 8


class Purpose(StrEnum):
    SESSION = "session"
    CSRF = "csrf"
    INVITE = "invite"
    PAIRING_CODE = "pairing_code"
    ACCESS = "access"
    REFRESH = "refresh"
    WORKER = "worker"
    SERVICE = "service"
    BACKUP_READER = "backup_reader"
    TELEGRAM_LINK = "telegram_link"
    UNDO = "undo"


def new_token() -> str:
    """Случайный токен: 32 байта, base64url без выравнивания — 43 символа."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def is_pairing_code(value: str) -> bool:
    return len(value) == PAIRING_CODE_LENGTH and all(char in PAIRING_CODE_ALPHABET for char in value)


def keyed_hash(key: bytes, purpose: Purpose, value: str) -> str:
    """HMAC-SHA256 значения с назначением; шестнадцатеричная строка из 64 символов."""
    return _hmac(key, purpose, value).hex()


def derive_token(key: bytes, purpose: Purpose, parent: str) -> str:
    """Токен, однозначно выводимый из родителя; без ключа не предсказуем."""
    return base64.urlsafe_b64encode(_hmac(key, purpose, "derive:" + parent)).rstrip(b"=").decode("ascii")


def same_secret(first: str, second: str) -> bool:
    """Сравнение секретов за постоянное время."""
    return hmac.compare_digest(first.encode("utf-8"), second.encode("utf-8"))


def _hmac(key: bytes, purpose: Purpose, value: str) -> bytes:
    if not key:
        raise ValueError("Пустой серверный ключ")
    message = purpose.value.encode("ascii") + b"\x00" + value.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).digest()
