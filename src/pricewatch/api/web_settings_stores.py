"""Настройки «Магазины» (администратор, SITE_DESIGN_SPEC.md, раздел 8).

Название и цвет меняются в строке; включение, отключение и удаление — после подтверждения в окне.
Ответ — страница целиком, htmx берёт список (#page-rest) и сообщение. Права проверяет application.stores_admin.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.application.dashboard import run_status
from pricewatch.application.ports import StoreOverview
from pricewatch.application.stores_admin import COLORS, change_store, delete_store, set_store_enabled, stores_overview
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
PAGE = "/web/settings/stores"


@router.get(PAGE)
def stores_page(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(_page(request, session))


@router.get("/web/stores/{store_id}/edit")
def store_form(request: Request, store_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(render("fragments/store_form.html", store=_store(request, session, store_id), colors=COLORS))


@router.get("/web/stores/{store_id}/toggle")
def toggle_dialog(request: Request, store_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(render("fragments/store_confirm.html", store=_store(request, session, store_id), action="toggle"))


@router.get("/web/stores/{store_id}/delete")
def delete_dialog(request: Request, store_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    store = _store(request, session, store_id)
    if store.used:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"store": "in_use"}})
    return HTMLResponse(render("fragments/store_confirm.html", store=store, action="delete"))


@router.post("/web/stores/{store_id}/edit")
async def store_edit(request: Request, store_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    await run_in_threadpool(
        lambda: change_store(context.unit_of_work(), session.user, store_id, name=form.get("name", ""), color=form.get("color", ""), now=context.clock())
    )
    return HTMLResponse(await run_in_threadpool(_page, request, session))


@router.post("/web/stores/{store_id}/toggle")
async def store_toggle(request: Request, store_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    if form.get("enabled") not in ("true", "false"):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"enabled": "invalid"}})
    context = get_context(request)
    await run_in_threadpool(set_store_enabled, context.unit_of_work(), session.user, store_id, form["enabled"] == "true", context.clock())
    return HTMLResponse(await run_in_threadpool(_page, request, session))


@router.post("/web/stores/{store_id}/delete")
async def store_delete(request: Request, store_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    await run_in_threadpool(delete_store, context.unit_of_work(), session.user, store_id, context.clock())
    return HTMLResponse(await run_in_threadpool(_page, request, session))


def _page(request: Request, session: SessionUser) -> str:
    context = get_context(request)
    stores = stores_overview(context.unit_of_work(), session.user)
    return render("settings_stores.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()), stores=stores)


def _store(request: Request, session: SessionUser, store_id: int) -> StoreOverview:
    found = next((store for store in stores_overview(get_context(request).unit_of_work(), session.user) if store.store_id == store_id), None)
    if found is None:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return found
