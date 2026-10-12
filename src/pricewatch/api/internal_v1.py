"""Внутренний API для Telegram-бота (DEVELOPMENT_ARCHITECTURE.md, разделы 3, 4 и 10): /internal/v1.

Только Authorization: Bearer с токеном сервиса; токены приложения, worker и cookie сайта здесь не действуют.
Каждый запрос отмечает пульс бота (сторож видит, что бот жив). В контракт приложения не входит.
- GET  /ping                         — API доступен (бот опрашивает раз в минуту);
- POST /telegram/link                — привязать чат по коду с сайта;
- POST /telegram/pairing-code        — код для приложения привязанному пользователю;
- POST /outbox/claim                 — взять сообщения на отправку;
- POST /outbox/{id}/sent | /failed   — отправлено или не удалось (повтор позже).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from pricewatch.api.app_auth import bearer_token
from pricewatch.api.context import get_context
from pricewatch.api.v1_json import utc_iso
from pricewatch.application.services import authenticate_service
from pricewatch.application.telegram import BATCH_MAX, claim_messages, link_chat, message_failed, message_sent, pairing_code_for_chat

router = APIRouter(prefix="/internal/v1")

ChatId = Annotated[int, Field(ge=-(2**62), le=2**62)]
EntityId = Annotated[int, Path(ge=1, le=2**62)]


def current_service(request: Request) -> int:
    context = get_context(request)
    return authenticate_service(context.unit_of_work(), context.secret_key, bearer_token(request), context.clock())


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: Annotated[str, Field(min_length=1, max_length=64)]
    chat_id: ChatId


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chat_id: ChatId


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    limit: Annotated[int, Field(ge=1, le=BATCH_MAX)]


@router.get("/ping")
def ping(_service: int = Depends(current_service)) -> dict[str, bool]:
    return {"ok": True}


@router.post("/telegram/link")
async def telegram_link(body: LinkIn, request: Request, _service: int = Depends(current_service)) -> dict[str, str]:
    context = get_context(request)
    user = await run_in_threadpool(link_chat, context.unit_of_work(), context.secret_key, body.code, body.chat_id, context.clock())
    return {"display_name": user.display_name}


@router.post("/telegram/pairing-code")
async def telegram_pairing_code(body: ChatIn, request: Request, _service: int = Depends(current_service)) -> dict[str, str]:
    context = get_context(request)
    issued = await run_in_threadpool(pairing_code_for_chat, context.unit_of_work, context.secret_key, body.chat_id, context.clock())
    return {"code": issued.code, "expires_at": utc_iso(issued.expires_at)}


@router.post("/outbox/claim")
async def outbox_claim(body: ClaimIn, request: Request, _service: int = Depends(current_service)) -> dict[str, object]:
    context = get_context(request)
    messages = await run_in_threadpool(claim_messages, context.unit_of_work(), context.clock(), body.limit)
    return {"items": [{"id": message.notification_id, "chat_id": message.chat_id, "text": message.text} for message in messages]}


@router.post("/outbox/{notification_id}/sent", status_code=204)
async def outbox_sent(notification_id: EntityId, request: Request, _service: int = Depends(current_service)) -> Response:
    context = get_context(request)
    await run_in_threadpool(message_sent, context.unit_of_work(), notification_id, context.clock())
    return Response(status_code=204)


@router.post("/outbox/{notification_id}/failed", status_code=204)
async def outbox_failed(notification_id: EntityId, request: Request, _service: int = Depends(current_service)) -> Response:
    context = get_context(request)
    await run_in_threadpool(message_failed, context.unit_of_work(), notification_id, context.clock())
    return Response(status_code=204)
