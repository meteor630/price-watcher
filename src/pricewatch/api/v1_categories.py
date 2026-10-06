"""Категория для приложения: GET /api/v1/categories/{id} и /categories/{id}/chart по openapi/v1.yaml.

Ответ категории несёт ETag; тот же ETag в If-None-Match даёт 304. Чужая и удалённая категория — 404.
"""

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.v1_json import change_json, item_row_json, point_json
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.category import CategoryDetail, category_chart, category_detail
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period

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
