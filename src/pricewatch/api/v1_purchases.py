"""Покупки и архив для приложения: GET /api/v1/purchases и /archive по openapi/v1.yaml.

Сводка и разбивка по категориям — за весь период целиком, список покупок и архив — страницами
с непрозрачным курсором (смещение в отсортированном списке).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.v1_json import item_row_json, next_cursor, offset_from_cursor, purchase_json, utc_iso
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.purchases import archive_list, purchases_overview
from pricewatch.domain.days import Period

router = APIRouter(prefix="/api/v1")

PRIVATE = "private, no-cache"
Cursor = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/purchases")
def purchases_route(
    request: Request, period: Period = Period.MONTH, cursor: Cursor = None, limit: Limit = 50, user: DeviceUser = Depends(app_user)
) -> JSONResponse:
    context = get_context(request)
    offset = offset_from_cursor(cursor)
    overview = purchases_overview(context.unit_of_work(), user.user, period, context.clock())
    body = {
        "period": overview.period.value,
        "count": overview.count,
        "spent_minor": overview.spent_minor,
        "spent_text": overview.spent_text,
        "savings": _savings(overview.savings_minor, overview.savings_text),
        "categories": [
            {
                "category": {"id": spending.category_id, "name": spending.name},
                "count": spending.count,
                "spent_minor": spending.spent_minor,
                "spent_text": spending.spent_text,
                "savings": _savings(spending.savings_minor, spending.savings_text),
            }
            for spending in overview.categories
        ],
        "items": [
            {
                "purchase": purchase_json(entry.view),
                "category": {"id": entry.category_id, "name": entry.category_name},
                "savings": _savings(entry.savings_minor, entry.savings_text),
            }
            for entry in overview.entries[offset:offset + limit]
        ],
        "next_cursor": next_cursor(offset, limit, len(overview.entries)),
    }
    return JSONResponse(body, headers={"Cache-Control": PRIVATE})


@router.get("/archive")
def archive_route(request: Request, cursor: Cursor = None, limit: Limit = 50, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    offset = offset_from_cursor(cursor)
    entries = archive_list(context.unit_of_work(), user.user, context.clock())
    body = {
        "items": [
            {
                "item": item_row_json(entry.row),
                "category": {"id": entry.category_id, "name": entry.category_name},
                "archived_at": utc_iso(entry.archived_at),
            }
            for entry in entries[offset:offset + limit]
        ],
        "next_cursor": next_cursor(offset, limit, len(entries)),
    }
    return JSONResponse(body, headers={"Cache-Control": PRIVATE})


def _savings(amount: int | None, text: str | None) -> dict[str, Any] | None:
    return None if amount is None else {"savings_minor": amount, "savings_text": text}
