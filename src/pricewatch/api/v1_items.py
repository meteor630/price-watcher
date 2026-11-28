"""Товар для приложения: GET и PATCH /api/v1/items/{id}, GET /items/{id}/chart по openapi/v1.yaml.

PATCH меняет количество и отметку «в итоге» по версии записи и требует Idempotency-Key.
Конфликт версий (409) несёт в details.current актуальную карточку товара.
"""

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.api.v1_json import point_json, purchase_json, store_json, utc_iso
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.item import ItemDetail, item_chart, item_detail, update_item_settings
from pricewatch.application.manual_checks import ItemCheckState, check_item, item_check_state
from pricewatch.application.ports import UNCHANGED
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

router = APIRouter(prefix="/api/v1")

EntityId = Annotated[int, Path(ge=1, le=2**62)]
PRIVATE = "private, no-cache"


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Annotated[int, Field(ge=1)]
    quantity: Annotated[int, Field(ge=1, le=999)] | None = None
    in_total: bool | None = None


@router.get("/items/{item_id}")
def item_route(request: Request, item_id: EntityId, user: DeviceUser = Depends(app_user)) -> Response:
    context = get_context(request)
    body = item_json(item_detail(context.unit_of_work(), user.user, item_id, context.clock()))
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": PRIVATE})
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": PRIVATE})


@router.patch("/items/{item_id}")
def item_patch_route(request: Request, item_id: EntityId, patch: ItemPatch, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    if patch.quantity is None and patch.in_total is None:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"body": "nothing_to_change"}})
    context = get_context(request)

    def run() -> dict[str, Any]:
        try:
            changed = update_item_settings(
                context.unit_of_work(), user.user, item_id, patch.version, context.clock(),
                quantity=UNCHANGED if patch.quantity is None else patch.quantity,
                in_total=UNCHANGED if patch.in_total is None else patch.in_total,
            )
        except PriceWatcherError as error:
            if error.code is not ErrorCode.VERSION_CONFLICT:
                raise
            current = item_json(item_detail(context.unit_of_work(), user.user, item_id, context.clock()))
            raise PriceWatcherError(ErrorCode.VERSION_CONFLICT, {"current": current}) from error
        return item_json(changed)

    return idempotent(request, user.user.id, patch.model_dump(exclude_none=True), run)


@router.get("/items/{item_id}/chart")
def item_chart_route(
    request: Request,
    item_id: EntityId,
    store_id: Annotated[int, Query(ge=1, le=2**62)],
    range: ChartRange = ChartRange.MONTH,  # noqa: A002 — имя параметра из контракта
    user: DeviceUser = Depends(app_user),
) -> JSONResponse:
    context = get_context(request)
    points = item_chart(context.unit_of_work(), user.user, item_id, store_id, range, context.clock())
    return JSONResponse({"range": range.value, "points": [point_json(point) for point in points]}, headers={"Cache-Control": PRIVATE})


def item_json(detail: ItemDetail) -> dict[str, Any]:
    return {
        "id": detail.item_id,
        "version": detail.version,
        "name": detail.name,
        "full_name": detail.full_name,
        "category": {"id": detail.category_id, "name": detail.category_name},
        "group": None if detail.group_id is None else {"id": detail.group_id, "name": detail.group_name},
        "quantity": detail.quantity,
        "in_total": detail.in_total,
        "photo_url": detail.photo_url,
        "price_status": detail.price_status.value,
        "under_review": detail.under_review,
        "best_store": store_json(detail.best_store),
        "unit_price_minor": detail.unit_price_minor,
        "unit_price_text": detail.unit_price_text,
        "regular_price_text": detail.regular_price_text,
        "failure_reason": detail.failure_reason,
        "offers": [
            {
                "store": store_json(offer.store),
                "url": offer.url,
                "price_status": offer.price_status.value,
                "availability": offer.availability,
                "price_minor": offer.price_minor,
                "price_text": offer.price_text,
                "card_price_minor": offer.card_price_minor,
                "card_price_text": offer.card_price_text,
                "checked_at": None if offer.checked_at is None else utc_iso(offer.checked_at),
                "is_best": offer.is_best,
            }
            for offer in detail.offers
        ],
        "history": [
            {"at": utc_iso(event.at), "store": store_json(event.store), "kind": event.kind.value, "text": event.text} for event in detail.history
        ],
        "state": detail.state.value,
        "purchase": purchase_json(detail.purchase),
        "cheaper_now_minor": detail.cheaper_now_minor,
        "cheaper_now_text": detail.cheaper_now_text,
        "archived_at": None if detail.archived_at is None else utc_iso(detail.archived_at),
    }


def _check_json(state: ItemCheckState, queued_tasks: int) -> dict[str, object]:
    return {
        "checking": state.checking, "queued_tasks": queued_tasks, "worker_online": state.worker_online,
        "last_checked_at": utc_iso(state.last_checked_at) if state.last_checked_at is not None else None,
        "best_price_minor": state.best_price_minor,
    }


@router.post("/items/{item_id}/check", status_code=202)
def item_check_route(request: Request, item_id: EntityId, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    """«Проверить цену»: те же правила, что на сайте (application.manual_checks)."""
    context = get_context(request)

    def run() -> dict[str, object]:
        queued = check_item(context.unit_of_work(), user.user, item_id, context.clock())
        return _check_json(item_check_state(context.unit_of_work(), user.user, item_id, context.clock()), len(queued.new_task_ids))

    return idempotent(request, user.user.id, {"item_id": item_id}, run, status=202)


@router.get("/items/{item_id}/check")
def item_check_state_route(request: Request, item_id: EntityId, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    state = item_check_state(context.unit_of_work(), user.user, item_id, context.clock())
    return JSONResponse(_check_json(state, 0), headers={"Cache-Control": PRIVATE})
