"""Ключ повтора для изменяющих запросов /api/v1 (заголовок Idempotency-Key, openapi/v1.yaml).

Хеш запроса — метод, путь и тело в каноническом виде: тот же ключ с другим телом — 409.
Ошибка запроса освобождает ключ (исправленный запрос можно отправить с ним же).
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from pricewatch.api.context import get_context
from pricewatch.application.idempotency import abandon, begin, finish
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

HEADER = "idempotency-key"
KEY_MIN, KEY_MAX = 8, 64


def idempotent(
    request: Request, user_id: int, body: dict[str, Any], run: Callable[[], dict[str, Any]], status: int = 200, *, one_time: bool = False
) -> JSONResponse:
    """`status` — код успешного ответа (202 у принятых к выполнению); повтор получает тот же код и тело."""
    key = request.headers.get(HEADER, "")
    if not KEY_MIN <= len(key) <= KEY_MAX or not key.isprintable():
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"Idempotency-Key": "required"}})
    canonical = json.dumps({"method": request.method, "path": request.url.path, "body": body}, sort_keys=True, ensure_ascii=False)
    request_hash = hashlib.sha256(canonical.encode()).hexdigest()
    context = get_context(request)
    replay = begin(context.unit_of_work(), user_id, key, request_hash, context.clock())
    if replay is not None:
        if one_time:
            raise PriceWatcherError(ErrorCode.IDEMPOTENCY_KEY_REUSED)
        return JSONResponse(replay.body, status_code=replay.status, headers={"Idempotency-Replayed": "true"})
    try:
        result = run()
    except BaseException:
        abandon(context.unit_of_work(), user_id, key)
        raise
    if one_time:
        # Коды и ссылки выдаются один раз, без сохранения секрета в журнале повторов.
        finish(context.unit_of_work(), user_id, key, status, {})
    else:
        finish(context.unit_of_work(), user_id, key, status, result)
    return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"} if one_time else None)
