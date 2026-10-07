"""Проверка цен для приложения: GET /api/v1/runs/current, POST /runs и /runs/{id}/stop по openapi/v1.yaml.

Запустить и остановить может только действующий администратор (application.runs). Запуск во время
идущей проверки — 409 RUN_IN_PROGRESS с её прогрессом. Изменяющие запросы — с ключом повтора, ответ 202.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.api.v1_dashboard import run_json
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.dashboard import run_status
from pricewatch.application.runs import start_manual_run, stop_run

router = APIRouter(prefix="/api/v1")

EntityId = Annotated[int, Path(ge=1, le=2**62)]
ACCEPTED = 202


@router.get("/runs/current")
def current_run(request: Request, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return JSONResponse(run_json(run_status(context.unit_of_work(), user.user, context.clock())), headers={"Cache-Control": "private, no-cache"})


@router.post("/runs")
def start_run(request: Request, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def run() -> dict[str, Any]:
        start_manual_run(context.unit_of_work(), user.user.id, context.clock())
        return run_json(run_status(context.unit_of_work(), user.user, context.clock()))

    return idempotent(request, user.user.id, {}, run, status=ACCEPTED)


@router.post("/runs/{run_id}/stop")
def stop(request: Request, run_id: EntityId, user: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def run() -> dict[str, Any]:
        stop_run(context.unit_of_work(), user.user.id, run_id, context.clock())
        return run_json(run_status(context.unit_of_work(), user.user, context.clock()))

    return idempotent(request, user.user.id, {}, run, status=ACCEPTED)
