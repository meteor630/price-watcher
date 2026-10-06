"""Категории для приложения: GET /api/v1/categories/{id}, /chart, PUT /categories/order и /groups/{id}/items/order по openapi/v1.yaml.

Ответ категории несёт ETag; тот же ETag в If-None-Match даёт 304. Чужая и удалённая категория — 404.
"""

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.api.v1_json import change_json, item_row_json, point_json
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.category import CategoryDetail, category_chart, category_detail
from pricewatch.application.shopping import reorder_categories, reorder_group_items
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

router = APIRouter(prefix="/api/v1")

CategoryId = Annotated[int, Path(ge=1, le=2**62)]
PRIVATE = "private, no-cache"


@router.get("/categories/{category_id}")
def category_route(request: Request, category_id: CategoryId, period: Period = Period.DAY, user: DeviceUser = Depends(app_user)) -> Response:
    context = get_context(request)
    body = category_json(category_detail(context.unit_of_work(), user.user, category_id, period, context.clock()))
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": PRIVATE})
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": PRIVATE})


class ReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ids: Annotated[list[Annotated[int, Field(ge=1, le=2**62)]], Field(min_length=1, max_length=500)]
    version: Annotated[int, Field(ge=1)]


@router.put("/categories/order")
def categories_order_route(request: Request, body: ReorderRequest, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    """Полный упорядоченный список категорий и версия порядка из /dashboard; ответ — новый порядок."""
    if len(set(body.ids)) != len(body.ids):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"ids": "not_unique"}})
    context = get_context(request)

    def run() -> dict[str, Any]:
        version = reorder_categories(context.unit_of_work(), user.user, body.ids, body.version)
        return {"ids": body.ids, "version": version}

    return idempotent(request, user.user.id, body.model_dump(), run)


@router.put("/groups/{group_id}/items/order")
def group_items_order_route(request: Request, group_id: CategoryId, body: ReorderRequest, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    """Полный упорядоченный список товаров группы и версия группы; ответ — новый порядок и версия."""
    if len(set(body.ids)) != len(body.ids):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"ids": "not_unique"}})
    context = get_context(request)

    def run() -> dict[str, Any]:
        version = reorder_group_items(context.unit_of_work(), user.user, group_id, body.ids, body.version)
        return {"ids": body.ids, "version": version}

    return idempotent(request, user.user.id, body.model_dump(), run)


@router.get("/categories/{category_id}/chart")
def category_chart_route(
    request: Request, category_id: CategoryId, range: ChartRange = ChartRange.MONTH, user: DeviceUser = Depends(app_user)  # noqa: A002 — имя параметра из контракта
) -> JSONResponse:
    context = get_context(request)
    points = category_chart(context.unit_of_work(), user.user, category_id, range, context.clock())
    return JSONResponse({"range": range.value, "points": [point_json(point) for point in points]}, headers={"Cache-Control": PRIVATE})


def category_json(detail: CategoryDetail) -> dict[str, Any]:
    return {
        "id": detail.category_id,
        "name": detail.name,
        "version": detail.version,
        "period": detail.period.value,
        "total_minor": detail.total_minor,
        "total_text": detail.total_text,
        "change": change_json(detail.change),
        "groups": [
            {"id": block.group_id, "name": block.name, "version": block.version, "items": [item_row_json(row) for row in block.items]}
            for block in detail.groups
        ],
    }
