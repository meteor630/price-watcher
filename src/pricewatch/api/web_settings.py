"""Настройки сайта: категории и группы (SITE_DESIGN_SPEC.md, раздел 8).

Каждое изменение — htmx-запрос с CSRF (общая проверка /web). Ответ — обновлённая страница, из которой
htmx берёт список (#page-rest) и сообщение (#flash). Удаление показывает сообщение с «Отменить»:
кнопка живёт 3 секунды, сервер принимает отмену 15. Конфликт версий не теряет страницу — показаны
актуальные данные и сообщение «Запись уже изменили…». Порядок приходит полным списком через запятую
и версией родителя — и от кнопок «выше/ниже», и от перетаскивания (app.js).
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, RedirectResponse, Response

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.api.web_home import PANEL_TITLES
from pricewatch.application.category import CategoryOverview, GroupOverview, categories_overview
from pricewatch.application.dashboard import dashboard, run_status
from pricewatch.application.shopping import (
    create_category, create_group, delete_category, delete_group, rename_category, rename_group, reorder_categories, reorder_groups, undo_delete,
)
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.days import Period
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
SETTINGS_PATH = "/web/settings/categories"
IDS_MAX = 500


@router.get("/web/settings")
def settings_root() -> RedirectResponse:
    return RedirectResponse("/web/settings/items", status_code=302)


@router.get(SETTINGS_PATH)
def categories_page(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(_settings_page(request, session))


@router.get("/web/categories/{category_id}/rename")
def category_rename_form(request: Request, category_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    category = _overview_entry(request, session, category_id)
    return HTMLResponse(render("fragments/rename_form.html", action=f"/web/categories/{category_id}/rename", name=category.name, version=category.version))


@router.get("/web/groups/{group_id}/rename")
def group_rename_form(request: Request, group_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    group = _group_entry(request, session, group_id)
    return HTMLResponse(render("fragments/rename_form.html", action=f"/web/groups/{group_id}/rename", name=group.name, version=group.version))


@router.get("/web/categories/{category_id}/delete")
def category_delete_dialog(request: Request, category_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    overview = categories_overview(context.unit_of_work(), session.user, context.clock())
    category = _overview_entry(request, session, category_id)
    others = [entry for entry in overview.categories if entry.category_id != category_id]
    return HTMLResponse(render("fragments/delete_category.html", category=category, others=others))


@router.get("/web/groups/{group_id}/delete")
def group_delete_dialog(request: Request, group_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(render("fragments/delete_group.html", group=_group_entry(request, session, group_id)))


@router.post(SETTINGS_PATH)
async def category_create(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    await run_in_threadpool(create_category, context.unit_of_work(), session.user, form.get("name", ""))
    return HTMLResponse(await run_in_threadpool(_settings_page, request, session))


@router.post("/web/categories/{category_id}/rename")
async def category_rename(request: Request, category_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    return await _change(request, session, lambda: rename_category(context.unit_of_work(), session.user, category_id, form.get("name", ""), _version(form)))


@router.post("/web/categories/{category_id}/delete")
async def category_delete(request: Request, category_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    choice = form.get("choice", "delete")
    if choice not in ("move", "delete"):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"choice": "invalid"}})
    target = None
    if choice == "move":
        move_to = form.get("move_to", "")
        if not move_to.isdigit():
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"move_to": "invalid"}})
        target = int(move_to)

    def run() -> str:
        return delete_category(context.unit_of_work(), context.secret_key, session.user, category_id, _version(form), target, context.clock())

    return await _change(request, session, run, deleted="Категория удалена.")


@router.post("/web/categories/order")
async def categories_order(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    ids = _ids(form)
    view = form.get("view", "settings")
    if view not in ("settings", "home"):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"view": "invalid"}})
    flash = None
    try:
        await run_in_threadpool(reorder_categories, context.unit_of_work(), session.user, ids, _version(form))
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    if view == "home":
        try:
            period = Period(form.get("period", "day"))
        except ValueError as error:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"period": "invalid"}}) from error
        return HTMLResponse(await run_in_threadpool(_home_page, request, session, period, flash))
    return HTMLResponse(await run_in_threadpool(_settings_page, request, session, flash))


@router.post("/web/categories/{category_id}/groups")
async def group_create(request: Request, category_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    await run_in_threadpool(create_group, context.unit_of_work(), session.user, category_id, form.get("name", ""))
    return HTMLResponse(await run_in_threadpool(_settings_page, request, session))


@router.post("/web/categories/{category_id}/groups/order")
async def groups_order(request: Request, category_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    return await _change(request, session, lambda: reorder_groups(context.unit_of_work(), session.user, category_id, _ids(form), _version(form)))


@router.post("/web/groups/{group_id}/rename")
async def group_rename(request: Request, group_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    return await _change(request, session, lambda: rename_group(context.unit_of_work(), session.user, group_id, form.get("name", ""), _version(form)))


@router.post("/web/groups/{group_id}/delete")
async def group_delete(request: Request, group_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    if form.get("items") not in ("keep", "delete"):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"items": "invalid"}})

    def run() -> str:
        return delete_group(context.unit_of_work(), context.secret_key, session.user, group_id, _version(form), form["items"] == "keep", context.clock())

    return await _change(request, session, run, deleted="Группа удалена.")


@router.post("/web/undo")
async def undo(request: Request, session: SessionUser = Depends(web_user)) -> Response:
    form = await read_form(request)
    context = get_context(request)
    await run_in_threadpool(undo_delete, context.unit_of_work(), context.secret_key, session.user, form.get("token", ""), context.clock())
    # Отмена возможна с любой страницы: страница перечитывается целиком.
    return Response(status_code=204, headers={"HX-Refresh": "true"})


async def _change(request: Request, session: SessionUser, action: Callable[[], object], deleted: str | None = None) -> HTMLResponse:
    flash = None
    undo_token = None
    try:
        result = await run_in_threadpool(action)
        if deleted is not None and isinstance(result, str):
            undo_token = result
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    html = await run_in_threadpool(_settings_page, request, session, flash, deleted if undo_token else None, undo_token)
    return HTMLResponse(html)


def _version(form: dict[str, str]) -> int:
    raw = form.get("version", "")
    if not raw.isdigit() or not 1 <= int(raw) <= 2**62:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"version": "invalid"}})
    return int(raw)


def _ids(form: dict[str, str]) -> list[int]:
    parts = form.get("ids", "").split(",")
    if not parts or len(parts) > IDS_MAX or not all(part.isdigit() and 1 <= int(part) <= 2**62 for part in parts):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"ids": "invalid"}})
    ids = [int(part) for part in parts]
    if len(set(ids)) != len(ids):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"ids": "not_unique"}})
    return ids


def _overview_entry(request: Request, session: SessionUser, category_id: int) -> CategoryOverview:
    context = get_context(request)
    overview = categories_overview(context.unit_of_work(), session.user, context.clock())
    for entry in overview.categories:
        if entry.category_id == category_id:
            return entry
    raise PriceWatcherError(ErrorCode.NOT_FOUND)


def _group_entry(request: Request, session: SessionUser, group_id: int) -> GroupOverview:
    context = get_context(request)
    overview = categories_overview(context.unit_of_work(), session.user, context.clock())
    for entry in overview.categories:
        for group in entry.groups:
            if group.group_id == group_id:
                return group
    raise PriceWatcherError(ErrorCode.NOT_FOUND)


def _settings_page(request: Request, session: SessionUser, flash: str | None = None, deleted: str | None = None, undo_token: str | None = None) -> str:
    context = get_context(request)
    overview = categories_overview(context.unit_of_work(), session.user, context.clock())
    return render(
        "settings_categories.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user),
        overview=overview, flash_message=flash, toast=deleted, undo_token=undo_token,
    )


def _home_page(request: Request, session: SessionUser, period: Period, flash: str | None) -> str:
    context = get_context(request)
    board = dashboard(context.unit_of_work(), session.user, period, context.clock())
    return render("home.html", session_user=session, board=board, header_run=board.run, panels=PANEL_TITLES, flash_message=flash)
