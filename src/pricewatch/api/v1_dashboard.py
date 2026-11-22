"""Главная для приложения: GET /api/v1/dashboard и /dashboard/items по openapi/v1.yaml.

Деньги — копейки и готовые строки с сервера. Ответ главной несёт ETag: тот же ETag в If-None-Match
даёт 304 без тела. Курсор списка непрозрачен для клиента; внутри — смещение в отсортированном списке.
"""

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.v1_json import change_json, offset_from_cursor, point_json, store_json, utc_iso
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.dashboard import CategoryCard, Dashboard, ItemChangeView, Panel, RunView, dashboard, dashboard_chart, panel_items
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period

router = APIRouter(prefix="/api/v1")

PRIVATE = "private, no-cache"


@router.get("/dashboard")
def dashboard_route(request: Request, period: Period = Period.DAY, user: DeviceUser = Depends(app_user)) -> Response:
    context = get_context(request)
    body = dashboard_json(dashboard(context.unit_of_work(), user.user, period, context.clock()))
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": PRIVATE})
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": PRIVATE})


@router.get("/dashboard/chart")
def chart_route(
    request: Request, range: ChartRange = ChartRange.MONTH, user: DeviceUser = Depends(app_user)  # noqa: A002
) -> JSONResponse:
    context = get_context(request)
    points = dashboard_chart(context.unit_of_work(), user.user, range, context.clock())
    return JSONResponse({"range": range.value, "points": [point_json(point) for point in points]}, headers={"Cache-Control": PRIVATE})


@router.get("/dashboard/items")
def dashboard_items_route(
    request: Request,
    panel: Panel,
    period: Period = Period.DAY,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    user: DeviceUser = Depends(app_user),
) -> JSONResponse:
    context = get_context(request)
    page = panel_items(context.unit_of_work(), user.user, panel, period, context.clock(), offset_from_cursor(cursor), limit)
    return JSONResponse(
        {
            "items": [item_change_json(item) for item in page.items],
            "next_cursor": None if page.next_offset is None else f"o{page.next_offset}",
        },
        headers={"Cache-Control": PRIVATE},
    )


def dashboard_json(board: Dashboard) -> dict[str, Any]:
    return {
        "as_of": board.as_of.isoformat(),
        "period": board.period.value,
        "total_minor": board.total_minor,
        "total_text": board.total_text,
        "change": change_json(board.change),
        "in_total_count": board.in_total_count,
        "watching_count": board.watching_count,
        "categories": [category_json(card) for card in board.categories],
        "categories_order_version": board.categories_order_version,
        "panels": {
            "tracked": board.panels.tracked,
            "cheaper": board.panels.cheaper,
            "pricier": board.panels.pricier,
            "out_of_stock": board.panels.out_of_stock,
        },
        "run": run_json(board.run),
        "reviews_pending": board.reviews_pending,
    }


def category_json(card: CategoryCard) -> dict[str, Any]:
    mover = card.top_mover
    return {
        "id": card.category_id,
        "name": card.name,
        "version": card.version,
        "in_total_count": card.in_total_count,
        "watching_count": card.watching_count,
        "total_minor": card.total_minor,
        "total_text": card.total_text,
        "change": change_json(card.change),
        "sparkline": [point_json(point) for point in card.sparkline],
        "top_mover": None if mover is None else {
            "item_id": mover.item_id, "name": mover.name, "percent_text": mover.percent_text, "direction": mover.direction.value,
        },
    }


def item_change_json(item: ItemChangeView) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "name": item.name,
        "store": store_json(item.store),
        "category": {"id": item.category_id, "name": item.category_name},
        "old_price_text": item.old_price_text,
        "new_price_text": item.new_price_text,
        "change": change_json(item.change),
    }


def run_json(run: RunView) -> dict[str, Any]:
    return {
        "id": run.run_id,
        "kind": run.kind,
        "status": run.status,
        "total": run.total,
        "done": run.done,
        "failed": run.failed,
        "started_at": None if run.started_at is None else utc_iso(run.started_at),
        "finished_at": None if run.finished_at is None else utc_iso(run.finished_at),
        "can_start": run.can_start,
        "can_stop": run.can_stop,
    }
