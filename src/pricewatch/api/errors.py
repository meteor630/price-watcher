"""Ответы об ошибках по схеме ErrorResponse из openapi/v1.yaml и HTTP-статусы кодов; на страницах сайта — HTML (web_errors)."""

import dataclasses
import logging
from collections.abc import Mapping
from datetime import date
from enum import Enum
from types import MappingProxyType

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response

from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.api.web_errors import is_web_path, web_error_response
from pricewatch.observability.ids import new_error_id

logger = logging.getLogger(__name__)

HTTP_STATUS: Mapping[ErrorCode, int] = MappingProxyType(
    {
        ErrorCode.INVALID_URL: 422,
        ErrorCode.STORE_NOT_ALLOWED: 403,
        ErrorCode.BLOCKED: 502,
        ErrorCode.CAPTCHA: 502,
        ErrorCode.TIMEOUT: 504,
        ErrorCode.LAYOUT_CHANGED: 422,
        ErrorCode.PRICE_NOT_FOUND: 422,
        ErrorCode.REGION_MISMATCH: 422,
        ErrorCode.UNSUPPORTED_CURRENCY: 422,
        ErrorCode.WORKER_OFFLINE: 503,
        ErrorCode.RUN_IN_PROGRESS: 409,
        ErrorCode.VERSION_CONFLICT: 409,
        ErrorCode.DB_ERROR: 503,
        ErrorCode.INTERNAL_ERROR: 500,
        ErrorCode.AUTH_REQUIRED: 401,
        ErrorCode.FORBIDDEN: 403,
        ErrorCode.NOT_FOUND: 404,
        ErrorCode.VALIDATION_ERROR: 422,
        ErrorCode.RATE_LIMITED: 429,
        ErrorCode.PAIRING_CODE_INVALID: 400,
        ErrorCode.PAIRING_CODE_EXPIRED: 400,
        ErrorCode.REFRESH_TOKEN_INVALID: 401,
        ErrorCode.REFRESH_TOKEN_REUSED: 401,
        ErrorCode.IDEMPOTENCY_KEY_REUSED: 409,
        ErrorCode.NAME_TAKEN: 409,
        ErrorCode.INVALID_CREDENTIALS: 401,
        ErrorCode.INVITE_INVALID: 400,
        ErrorCode.LOGIN_TAKEN: 409,
        ErrorCode.CSRF_FAILED: 403,
    }
)

# Ошибки Starlette без нашего кода: маршрут не найден, метод не поддерживается и т. п.
_HTTP_EXCEPTION_CODES: Mapping[int, ErrorCode] = MappingProxyType(
    {
        401: ErrorCode.AUTH_REQUIRED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.NOT_FOUND,
        429: ErrorCode.RATE_LIMITED,
    }
)


def error_response(
    code: ErrorCode,
    status: int,
    *,
    error_id: str | None = None,
    details: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, object] = {"code": code.value, "message": SAFE_MESSAGES[code], "error_id": error_id}
    extra_headers = dict(headers or {})
    rest = dict(details or {})
    # Время ожидания — отдельное поле ошибки по схеме ErrorResponse и стандартный заголовок Retry-After.
    retry_after = rest.pop("retry_after_seconds", None)
    if isinstance(retry_after, int):
        error["retry_after_seconds"] = retry_after
        extra_headers["Retry-After"] = str(retry_after)
    if rest:
        error["details"] = _jsonable(rest)
    return JSONResponse({"error": error}, status_code=status, headers=extra_headers)


def _jsonable(value: object) -> object:
    """Детали ошибки в JSON: записи domain (например, актуальное состояние при конфликте) — словарями."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


# Ответ 401 приложению называет схему входа (RFC 6750); сайт входит по cookie, ему заголовок не нужен.
_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


async def handle_project_error(request: Request, error: PriceWatcherError) -> Response:
    status = HTTP_STATUS[error.code]
    headers = _BEARER_CHALLENGE if status == 401 and request.url.path.startswith("/api/") else None
    error_id = None
    if status >= 500:
        # Сбой на нашей стороне (например, база): причина — в журнал под тем же error_id, что получит клиент.
        error_id = new_error_id()
        logger.error(
            "Сбой обслуживания запроса",
            exc_info=error,
            extra={"error_id": error_id, "error_code": error.code.value, "path": request.url.path},
        )
    if is_web_path(request.url.path):
        return web_error_response(error.code, status, htmx=_htmx(request), error_id=error_id, details=error.details)
    return error_response(error.code, status, error_id=error_id, details=error.details, headers=headers)


async def handle_validation_error(request: Request, error: RequestValidationError) -> Response:
    if is_web_path(request.url.path):
        return web_error_response(ErrorCode.VALIDATION_ERROR, 422, htmx=_htmx(request))
    # Только путь к полю и тип проблемы: введённые значения в ответ не возвращаются.
    fields = {
        ".".join(str(part) for part in issue["loc"]): issue["type"] for issue in error.errors()
    }
    return error_response(ErrorCode.VALIDATION_ERROR, 422, details={"fields": fields})


async def handle_http_exception(request: Request, error: HTTPException) -> Response:
    if error.status_code >= 500:
        code = ErrorCode.INTERNAL_ERROR
    else:
        code = _HTTP_EXCEPTION_CODES.get(error.status_code, ErrorCode.VALIDATION_ERROR)
    if is_web_path(request.url.path):
        return web_error_response(code, error.status_code, htmx=_htmx(request))
    return error_response(code, error.status_code, headers=error.headers)
