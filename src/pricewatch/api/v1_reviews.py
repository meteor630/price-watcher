"""«Требует решения» для приложения: GET /api/v1/reviews и POST /reviews/{id}/decision по openapi/v1.yaml.

Карточки — только по товарам пользователя (application.reviews). Решение — с ключом повтора;
уже решённая или чужая карточка — 404.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.api.v1_json import next_cursor, offset_from_cursor, store_json, utc_iso
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.reviews import ReviewCard, decide, review_cards
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.review import Decision, ReviewKind

router = APIRouter(prefix="/api/v1")

EntityId = Annotated[int, Path(ge=1, le=2**62)]


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["accept", "reject"]


@router.get("/reviews")
def reviews_route(
    request: Request,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    user: DeviceUser = Depends(app_user),
) -> JSONResponse:
    context = get_context(request)
    offset = offset_from_cursor(cursor)
    cards = review_cards(context.unit_of_work(), user.user, context.clock())
    body = {"items": [review_json(card, None) for card in cards[offset:offset + limit]], "next_cursor": next_cursor(offset, limit, len(cards))}
    return JSONResponse(body, headers={"Cache-Control": "private, no-cache"})


@router.post("/reviews/{review_id}/decision")
def decision_route(request: Request, review_id: EntityId, body: DecisionBody, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def run() -> dict[str, Any]:
        now = context.clock()
        card = next((card for card in review_cards(context.unit_of_work(), user.user, now) if card.review_id == review_id), None)
        if card is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        decision = Decision(body.decision)
        decide(context.unit_of_work(), context.secret_key, user.user, review_id, decision, now)
        return review_json(card, decision)

    return idempotent(request, user.user.id, body.model_dump(), run)


def review_json(card: ReviewCard, decision: Decision | None) -> dict[str, Any]:
    kind = ReviewKind.TITLE_CHANGED if ReviewKind.TITLE_CHANGED in card.kinds else ReviewKind.PRICE_JUMP
    before, after = (card.before_title, card.after_title) if kind is ReviewKind.TITLE_CHANGED else (card.before_price_text, card.after_price_text)
    first = card.items[0]
    return {
        "id": card.review_id,
        "kind": kind.value,
        "item_id": first.item_id,
        "item_name": first.name,
        "store": store_json(card.store),
        "url": card.url,
        "checked_at": utc_iso(card.checked_at),
        "before_text": before or "",
        "after_text": after or "",
        "decision": None if decision is None else decision.value,
        "kinds": sorted(value.value for value in card.kinds),
        "before_price_text": card.before_price_text,
        "after_price_text": card.after_price_text,
    }
