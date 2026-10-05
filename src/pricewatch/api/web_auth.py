"""Сессия сайта в HTTP: cookie, проверка сессии, Origin и CSRF.

/web/* принимает только cookie-сессию; заголовок Authorization здесь не читается.
Изменяющие запросы (не GET, HEAD, OPTIONS) требуют совпадения Origin с адресом сайта
и CSRF-токена в заголовке X-CSRF-Token.
"""

from fastapi import Request, Response

from pricewatch.api.context import get_context
from pricewatch.application.web_auth import SessionUser, authenticate_session, check_csrf
from pricewatch.domain.accounts import SESSION_LIFETIME
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

SESSION_COOKIE = "pw_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


def web_user(request: Request) -> SessionUser:
    """Зависимость маршрутов /web: пользователь сессии; для изменяющих запросов ещё Origin и CSRF."""
    context = get_context(request)
    token = request.cookies.get(SESSION_COOKIE)
    session_user = authenticate_session(context.unit_of_work(), context.secret_key, token, context.clock())
    if request.method not in SAFE_METHODS:
        if request.headers.get("origin") != request.app.state.public_origin:
            raise PriceWatcherError(ErrorCode.CSRF_FAILED)
        check_csrf(context.secret_key, token or "", request.headers.get(CSRF_HEADER))
    return session_user
