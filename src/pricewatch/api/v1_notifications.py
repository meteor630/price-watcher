"""Колокольчик администратора для приложения: GET /api/v1/notifications и POST /notifications/read по openapi/v1.yaml."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.api.v1_json import offset_from_cursor, utc_iso
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.notifications import READ_IDS_MAX, BellItem, bell_page, mark_read
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

router = APIRouter(prefix="/api/v1")


class ReadIds(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ids: Annotated[list[Annotated[int, Field(ge=1, le=2**62)]], Field(min_length=1, max_length=READ_IDS_MAX)]


class ReadAll(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    all: Literal[True]


@router.get("/notifications")
def notifications_route(
    request: Request,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    user: DeviceUser = Depends(app_user),
) -> JSONResponse:
    context = get_context(request)
    page = bell_page(context.unit_of_work(), user.user, offset_from_cursor(cursor), limit)
    body = {
        "items": [notification_json(item) for item in page.items],
        "next_cursor": None if page.next_offset is None else f"o{page.next_offset}",
        "unread_count": page.unread_count,
    }
    return JSONResponse(body, headers={"Cache-Control": "private, no-cache"})


@router.post("/notifications/read")
def read_route(request: Request, body: ReadIds | ReadAll, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    if isinstance(body, ReadIds) and len(set(body.ids)) != len(body.ids):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"ids": "not_unique"}})
    ids = body.ids if isinstance(body, ReadIds) else None

    def run() -> dict[str, Any]:
        return {"unread_count": mark_read(context.unit_of_work(), user.user, ids, context.clock())}

    return idempotent(request, user.user.id, body.model_dump(), run)


def notification_json(item: BellItem) -> dict[str, Any]:
    return {
        "id": item.notification_id,
        "kind": item.kind,
        "text": item.text,
        "created_at": utc_iso(item.created_at),
        "read": item.read,
        "repeat_count": item.repeat_count,
        "link": None,
    }
