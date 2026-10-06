"""Что приложению нужно от сборки: единицы работы, серверный ключ, хешер, часы, каталог картинок.

Передаётся из bootstrap в create_app; тесты подставляют свой контекст.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.ports import ImageStore, UnitOfWork


@dataclass(frozen=True, slots=True)
class ApiContext:
    unit_of_work: Callable[[], UnitOfWork]
    secret_key: bytes
    hasher: PasswordHasher
    clock: Callable[[], datetime]
    images: ImageStore


def get_context(request: Request) -> ApiContext:
    context = getattr(request.app.state, "context", None)
    if context is None:
        raise RuntimeError("Контекст приложения не передан в create_app")
    return context


def client_ip(request: Request) -> str | None:
    """IP посетителя, определённый внешним слоем с учётом доверенных прокси."""
    return request.scope.get("state", {}).get("client_ip")
