"""Настройки «Пользователи» (администратор, SITE_DESIGN_SPEC.md, раздел 8).

Блокировка, разблокировка и снятие прав администратора — после подтверждения в окне. Приглашение:
ссылка показывается один раз (в базе только отпечаток кода); неиспользованные отмечаются и удаляются.
Права и защита последнего администратора — в application.users_admin.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.application.accounts import create_invite
from pricewatch.application.dashboard import run_status
from pricewatch.application.users_admin import block_user, delete_invites, demote_admin, unblock_user, users_page
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.accounts import Role
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
PAGE = "/web/settings/users"
ACTIONS = ("block", "unblock", "demote")
# Почему действие с пользователем не выполнено — понятным текстом вместо общего «Проверьте поля».
REFUSALS = {
    "self": "С собой так нельзя — попросите другого администратора.",
    "last_admin": "Это последний действующий администратор — без него управлять сайтом будет некому.",
    "already_blocked": "Пользователь уже заблокирован.",
    "not_blocked": "Пользователь не заблокирован.",
    "not_admin": "Он уже не администратор.",
}


@router.get(PAGE)
def users_settings(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(_page(request, session))


@router.get("/web/users/{user_id}/{action}")
def confirm_dialog(request: Request, user_id: EntityId, action: str, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    if action not in ACTIONS:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    found = next((user for user in users_page(get_context(request).unit_of_work(), session.user).users if user.user_id == user_id), None)
    if found is None:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return HTMLResponse(render("fragments/user_confirm.html", user=found, action=action))


@router.post("/web/users/{user_id}/block")
async def block(request: Request, user_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    refusal = await _attempt(lambda: block_user(context.unit_of_work(), session.user, user_id, context.clock()))
    notice = None if refusal else "Пользователь заблокирован, вход закрыт."
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, notice=notice, flash=refusal)))


@router.post("/web/users/{user_id}/unblock")
async def unblock(request: Request, user_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    result = await run_in_threadpool(unblock_user, context.unit_of_work(), session.user, user_id, context.clock())
    if result.too_late:
        notice = "Пользователь разблокирован. Его списки уже стёрты окончательно — вернуть их нельзя."
    elif result.restored:
        notice = "Пользователь разблокирован, его списки возвращены."
    else:
        notice = "Пользователь разблокирован."
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, notice=notice)))


@router.post("/web/users/{user_id}/demote")
async def demote(request: Request, user_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    refusal = await _attempt(lambda: demote_admin(context.unit_of_work(), session.user, user_id, context.clock()))
    notice = None if refusal else "Права администратора сняты."
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, notice=notice, flash=refusal)))


@router.post("/web/invites")
async def invite(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    try:
        role = Role(form.get("role", ""))
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"role": "invalid"}}) from error
    context = get_context(request)
    issued = await run_in_threadpool(create_invite, context.unit_of_work(), context.secret_key, session.user.id, role, context.clock())
    link = {"url": f"{request.app.state.public_origin}/web/register?invite={issued.code}", "role": role.value, "expires_at": issued.invite.expires_at}
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, issued=link)))


@router.post("/web/invites/delete")
async def invites_delete(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    ids = []
    for name, value in form.items():
        number = name.removeprefix("invite_")
        if name == number or value != "on" or not number.isascii() or not number.isdigit() or not 1 <= int(number) <= 2**62:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {name: "invalid"}})
        ids.append(int(number))
    context = get_context(request)
    deleted = await run_in_threadpool(delete_invites, context.unit_of_work(), session.user, ids, context.clock())
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, notice=f"Удалено приглашений: {len(deleted)}.")))


async def _attempt(action: Callable[[], object]) -> str | None:
    """Выполнить действие; отказ по правилам (себя, последнего администратора…) — его текст, остальное — ошибкой."""
    try:
        await run_in_threadpool(action)
    except PriceWatcherError as error:
        fields = error.details.get("fields") if error.code is ErrorCode.VALIDATION_ERROR else None
        reason = fields.get("user") if isinstance(fields, dict) else None
        if reason not in REFUSALS:
            raise
        return REFUSALS[reason]
    return None


def _page(
    request: Request, session: SessionUser, *, notice: str | None = None, issued: dict[str, object] | None = None, flash: str | None = None
) -> str:
    context = get_context(request)
    page = users_page(context.unit_of_work(), session.user)
    return render(
        "settings_users.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()),
        page=page, notice=notice, issued=issued, now=context.clock(), flash_message=flash,
    )
