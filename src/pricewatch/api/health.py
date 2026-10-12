"""GET /healthz — жив ли сервер и отвечает ли база (Docker healthcheck, сторож). Без входа и без подробностей."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from pricewatch.api.context import get_context
from pricewatch.application.health import check_health

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    await run_in_threadpool(check_health, get_context(request).unit_of_work())
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
