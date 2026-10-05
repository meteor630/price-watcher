"""Bearer-токен приложения в HTTP: /api/v1/* принимает только Authorization: Bearer, cookie не читается."""

from fastapi import Request

from pricewatch.api.context import get_context
from pricewatch.application.app_auth import DeviceUser, authenticate_access


def bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def app_user(request: Request) -> DeviceUser:
    """Зависимость маршрутов /api/v1: пользователь и устройство по access-токену."""
    context = get_context(request)
    return authenticate_access(context.unit_of_work(), context.secret_key, bearer_token(request), context.clock())
