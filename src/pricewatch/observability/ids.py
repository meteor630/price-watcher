"""Идентификаторы запроса и инцидента.

request_id живёт в контексте выполнения: каждая запись журнала внутри запроса
получает его автоматически, в том числе в асинхронных задачах этого запроса.
"""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_ERROR_ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_ERROR_ID_LENGTH = 10

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


def new_error_id() -> str:
    """Короткий идентификатор инцидента для пользователя: E-XXXXXXXXXX без похожих символов."""
    return "E-" + "".join(secrets.choice(_ERROR_ID_ALPHABET) for _ in range(_ERROR_ID_LENGTH))
