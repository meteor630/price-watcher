"""Ответы об ошибках по схеме ErrorResponse из openapi/v1.yaml и HTTP-статусы кодов."""

from collections.abc import Mapping
from types import MappingProxyType

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError

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
    if details:
        error["details"] = dict(details)
    return JSONResponse({"error": error}, status_code=status, headers=dict(headers or {}))


async def handle_project_error(_: Request, error: PriceWatcherError) -> JSONResponse:
    return error_response(error.code, HTTP_STATUS[error.code], details=error.details)


async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    # Только путь к полю и тип проблемы: введённые значения в ответ не возвращаются.
    fields = {
        ".".join(str(part) for part in issue["loc"]): issue["type"] for issue in error.errors()
    }
    return error_response(ErrorCode.VALIDATION_ERROR, 422, details={"fields": fields})


async def handle_http_exception(_: Request, error: HTTPException) -> JSONResponse:
    if error.status_code >= 500:
        code = ErrorCode.INTERNAL_ERROR
    else:
        code = _HTTP_EXCEPTION_CODES.get(error.status_code, ErrorCode.VALIDATION_ERROR)
    return error_response(code, error.status_code, headers=error.headers)
