"""Проверка цен на сайте (SITE_DESIGN_SPEC.md, разделы 3 и 4): статус в шапке и блок запуска администратора.

- Статус в шапке и блок опрашивают сервер раз в 5 секунд, пока проверка идёт; ответ без опроса его прекращает.
- Запуск и остановка — htmx с CSRF (общая проверка /web); права проверяет application.runs.
  Запуск во время идущей проверки не ошибка для страницы: блок показывает её прогресс и сообщение.
- Ответ блока несёт и статус шапки (hx-swap-oob), чтобы они не расходились.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.application.dashboard import run_status
from pricewatch.application.runs import start_manual_run, stop_run
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]


@router.get("/web/runs/status")
def header_status(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    return HTMLResponse(render("fragments/run_status.html", run=run_status(context.unit_of_work(), session.user, context.clock())))


@router.get("/web/runs/panel")
def run_panel(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    _require_admin(session)
    return HTMLResponse(_panel(request, session, None))


@router.post("/web/runs")
async def start(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    message = None
    try:
        await run_in_threadpool(start_manual_run, context.unit_of_work(), session.user.id, context.clock())
    except PriceWatcherError as error:
        if error.code is not ErrorCode.RUN_IN_PROGRESS:
            raise
        message = SAFE_MESSAGES[error.code]
    return HTMLResponse(await run_in_threadpool(_panel, request, session, message))


@router.post("/web/runs/{run_id}/stop")
async def stop(request: Request, run_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    await run_in_threadpool(stop_run, context.unit_of_work(), session.user.id, run_id, context.clock())
    return HTMLResponse(await run_in_threadpool(_panel, request, session, None))


def _panel(request: Request, session: SessionUser, message: str | None) -> str:
    context = get_context(request)
    run = run_status(context.unit_of_work(), session.user, context.clock())
    return render("fragments/run_panel.html", run=run, message=message) + render("fragments/run_status.html", run=run, oob=True)


def _require_admin(session: SessionUser) -> None:
    # Блок только для администратора (раздел 4); запуск и остановку отдельно проверяет application.runs.
    if not session.user.is_admin:
        raise PriceWatcherError(ErrorCode.FORBIDDEN)
