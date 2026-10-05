"""Маршруты входа приложения и /me по openapi/v1.yaml."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import client_ip, get_context
from pricewatch.application.app_auth import DeviceUser, logout_device, pair, refresh
from pricewatch.domain.accounts import TokenPair

router = APIRouter(prefix="/api/v1")

# Ответы с токенами не должны оседать ни в каких кэшах (RFC 6749, раздел 5.1).
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, StringConstraints(pattern=r"^[A-Z2-7]{8}$")]
    device_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: Annotated[str, StringConstraints(min_length=1, max_length=200)]


def utc_iso(moment: datetime) -> str:
    """Дата-время для API — всегда UTC (openapi/v1.yaml, общие правила)."""
    return moment.astimezone(UTC).isoformat()


def token_response(tokens: TokenPair) -> JSONResponse:
    return JSONResponse(
        {
            "token_type": "Bearer",
            "access_token": tokens.access_token,
            "access_expires_at": utc_iso(tokens.access_expires_at),
            "refresh_token": tokens.refresh_token,
            "refresh_expires_at": utc_iso(tokens.refresh_expires_at),
        },
        headers=NO_STORE,
    )


@router.post("/auth/pair")
def pair_route(body: PairRequest, request: Request) -> JSONResponse:
    context = get_context(request)
    _, tokens = pair(context.unit_of_work(), context.secret_key, body.code, body.device_name, client_ip(request), context.clock())
    return token_response(tokens)


@router.post("/auth/refresh")
def refresh_route(body: RefreshRequest, request: Request) -> JSONResponse:
    context = get_context(request)
    return token_response(refresh(context.unit_of_work(), context.secret_key, body.refresh_token, context.clock()))


@router.post("/auth/logout", status_code=204)
def logout_route(request: Request, user: DeviceUser = Depends(app_user)) -> Response:
    context = get_context(request)
    logout_device(context.unit_of_work(), user.device_id, context.clock())
    return Response(status_code=204)


@router.get("/me")
def me_route(user: DeviceUser = Depends(app_user)) -> JSONResponse:
    body = {
        "id": user.user.id,
        "login": user.user.login,
        "display_name": user.user.display_name,
        "role": user.user.role.value,
    }
    etag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32] + '"'
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
