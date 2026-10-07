"""Колокольчик администратора на сайте: фрагмент для шапки и «Отметить все прочитанными»."""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.application.notifications import bell_page, mark_read
from pricewatch.application.web_auth import SessionUser
from pricewatch.web.templates import render

router = APIRouter()

BELL_SIZE = 20


@router.get("/web/notifications/bell")
def bell(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(_bell(request, session, opened=False))


@router.post("/web/notifications/read")
async def read_all(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    await run_in_threadpool(mark_read, context.unit_of_work(), session.user, None, context.clock())
    return HTMLResponse(await run_in_threadpool(_bell, request, session, True))


def _bell(request: Request, session: SessionUser, opened: bool) -> str:
    page = bell_page(get_context(request).unit_of_work(), session.user, 0, BELL_SIZE)
    return render("fragments/bell.html", page=page, open=opened)
