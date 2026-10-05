"""Внешний слой HTTP: request_id, защитные заголовки, необработанные исключения, журнал запросов.

Это чистое ASGI-промежуточное звено, самое внешнее из наших. Поэтому заголовки
получают все ответы, включая ошибки и 500, а необработанное исключение
превращается в ответ INTERNAL_ERROR раньше, чем дойдёт до Starlette.
"""

import logging
import re
import time
import uuid
from collections.abc import Mapping
from types import MappingProxyType

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pricewatch.api.client_ip import Network, client_ip
from pricewatch.api.errors import error_response
from pricewatch.domain.errors import ErrorCode
from pricewatch.observability.ids import new_error_id, request_context

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")

# Решение 7 этапа 1: всё только со своего домена, инлайн-скрипты и фреймы запрещены.
SECURITY_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
)


class EdgeMiddleware:
    def __init__(self, app: ASGIApp, trusted_proxies: tuple[Network, ...]) -> None:
        self.app = app
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        peer = scope["client"][0] if scope.get("client") else None
        forwarded_for = [value.decode("latin-1") for name, value in scope["headers"] if name == b"x-forwarded-for"]
        visitor_ip = client_ip(peer, forwarded_for, self.trusted_proxies)
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        with request_context(request_id):
            try:
                await self.app(scope, receive, send_with_headers)
            except Exception:
                error_id = new_error_id()
                logger.exception(
                    "Необработанное исключение",
                    extra={"error_id": error_id, "method": scope["method"], "path": scope["path"]},
                )
                if response_started:
                    raise
                response = error_response(ErrorCode.INTERNAL_ERROR, 500, error_id=error_id)
                await response(scope, receive, send_with_headers)
            finally:
                logger.info(
                    "HTTP-запрос",
                    extra={
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status,
                        "client_ip": str(visitor_ip) if visitor_ip else None,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )


def _request_id(scope: Scope) -> str:
    """Свой X-Request-ID клиента принимается, только если он похож на идентификатор."""
    for name, value in scope["headers"]:
        if name.decode("latin-1").lower() == REQUEST_ID_HEADER.lower():
            candidate = value.decode("latin-1")
            if _VALID_REQUEST_ID.fullmatch(candidate):
                return candidate
    return uuid.uuid4().hex
