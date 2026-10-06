"""Разбор форм сайта: application/x-www-form-urlencoded, как шлют обычная форма и htmx.

Тело читается частями с пределом размера; повтор поля и неизвестная кодировка — ошибка проверки.
Отдельная библиотека для multipart не нужна: файлов сайт через формы не принимает.
"""

from urllib.parse import parse_qsl

from fastapi import Request

from pricewatch.domain.errors import ErrorCode, PriceWatcherError

FORM_MAX_BYTES = 16 * 1024
FORM_MAX_FIELDS = 50
FORM_TYPE = "application/x-www-form-urlencoded"


async def read_form(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != FORM_TYPE:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"form": "content_type"}})
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > FORM_MAX_BYTES:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"form": "too_large"}})
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False, max_num_fields=FORM_MAX_FIELDS)
    except (UnicodeDecodeError, ValueError) as error:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"form": "invalid"}}) from error
    fields: dict[str, str] = {}
    for name, value in pairs:
        if name in fields:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {name: "repeated"}})
        fields[name] = value
    return fields
