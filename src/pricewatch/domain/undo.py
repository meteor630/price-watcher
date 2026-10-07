"""Токен отмены удаления (DEVELOPMENT_ARCHITECTURE.md, раздел 5, «Удаление и отмена»).

Интерфейс показывает «Отменить» 3 секунды, сервер принимает отмену 15 секунд — медленная сеть
не съедает нажатие. Токен не хранится: в нём вид записи, номер и срок, подписанные HMAC с серверным
ключом и номером пользователя. Чужой, подделанный и просроченный токены неотличимы — NOT_FOUND.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, keyed_hash, same_secret

UNDO_WINDOW = timedelta(seconds=15)
MAC_LENGTH = 32


class UndoKind(StrEnum):
    CATEGORY = "category"
    GROUP = "group"
    ITEM = "item"
    LINK = "link"
    # Отмена покупки и архива возвращает товар в список.
    PURCHASE = "purchase"
    ARCHIVE = "archive"
    # Решение по подозрительному изменению и поставленная перепроверка.
    DECISION = "decision"
    RECHECK = "recheck"


def issue_undo(key: bytes, user_id: int, kind: UndoKind, entity_id: int, now: datetime) -> str:
    expires = int((now + UNDO_WINDOW).timestamp())
    body = f"{kind.value}.{entity_id}.{expires}"
    return f"{body}.{_mac(key, user_id, body)}"


def read_undo(key: bytes, user_id: int, token: str, now: datetime) -> tuple[UndoKind, int]:
    parts = token.split(".")
    if len(parts) != 4 or not parts[1].isdigit() or not parts[2].isdigit():
        raise _gone()
    kind, entity_id, expires, mac = parts
    if not same_secret(_mac(key, user_id, f"{kind}.{entity_id}.{expires}"), mac):
        raise _gone()
    if now > datetime.fromtimestamp(int(expires), UTC) or kind not in {item.value for item in UndoKind}:
        raise _gone()
    return UndoKind(kind), int(entity_id)


def _mac(key: bytes, user_id: int, body: str) -> str:
    return keyed_hash(key, Purpose.UNDO, f"{user_id}.{body}")[:MAC_LENGTH]


def _gone() -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.NOT_FOUND, {"undo": "expired"})
