"""Настройки «Профиль» (SITE_DESIGN_SPEC.md, раздел 14): код для приложения, привязка Telegram, устройства, смена пароля, выход.

Все изменения — htmx с CSRF (общая проверка /web). Ошибки смены пароля — у полей; превышение попыток —
сообщение со временем ожидания, как на входе.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import client_ip, get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.application.app_auth import issue_pairing_code
from pricewatch.application.dashboard import run_status
from pricewatch.application.profile import change_password, devices, revoke_device
from pricewatch.application.telegram import is_linked, issue_link_code, unlink
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.accounts import PASSWORD_MAX, PASSWORD_MIN
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
PAGE = "/web/settings/profile"
PASSWORD_FIELDS = ("current_password", "password", "password_repeat")
FIELD_MESSAGES = {
    ("current_password", "wrong"): "Текущий пароль неверный.",
    ("password", "too_short"): f"Не короче {PASSWORD_MIN} символов.",
    ("password", "too_long"): f"Не длиннее {PASSWORD_MAX} символов.",
    ("password", "same_as_login"): "Пароль не должен совпадать с логином.",
    ("password_repeat", "mismatch"): "Пароли не совпадают.",
}


@router.get(PAGE)
def profile_page(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(_page(request, session))


@router.post("/web/profile/pairing-code")
async def pairing_code(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    issued = await run_in_threadpool(issue_pairing_code, context.unit_of_work(), context.secret_key, session.user.id, context.clock())
    return HTMLResponse(render("fragments/pairing_code.html", code=issued.code, expires_at=issued.expires_at))


@router.post("/web/profile/telegram/code")
async def telegram_code(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    issued = await run_in_threadpool(issue_link_code, context.unit_of_work(), context.secret_key, session.user, context.clock())
    return HTMLResponse(render("fragments/telegram_code.html", code=issued.code, expires_at=issued.expires_at, bot=request.app.state.telegram_bot))


@router.post("/web/profile/telegram/unlink")
async def telegram_unlink(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    await run_in_threadpool(unlink, context.unit_of_work(), session.user, context.clock())
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, notice="Telegram отвязан.")))


@router.get("/web/devices/{device_id}/revoke")
def revoke_dialog(request: Request, device_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    found = next((device for device in devices(get_context(request).unit_of_work(), session.user) if device.device_id == device_id), None)
    if found is None:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return HTMLResponse(render("fragments/device_confirm.html", device=found))


@router.post("/web/devices/{device_id}/revoke")
async def revoke(request: Request, device_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    await run_in_threadpool(revoke_device, context.unit_of_work(), session.user, device_id, context.clock())
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, notice="Доступ устройства отозван.")))


@router.post("/web/profile/password")
async def password(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    try:
        await run_in_threadpool(
            lambda: change_password(
                context.unit_of_work(), context.hasher, session, current=form.get("current_password", ""), new=form.get("password", ""),
                repeat=form.get("password_repeat", ""), ip=client_ip(request), now=context.clock(),
            )
        )
    except PriceWatcherError as error:
        fields = error.details.get("fields") if error.code is ErrorCode.VALIDATION_ERROR else None
        if not isinstance(fields, dict) or not all((name, reason) in FIELD_MESSAGES for name, reason in fields.items()):
            raise
        errors = {name: FIELD_MESSAGES[(name, reason)] for name, reason in fields.items()}
        return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, errors=errors)))
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, notice="Пароль изменён. Остальные сессии и приложения вышли.")))


def _page(request: Request, session: SessionUser, *, notice: str | None = None, errors: dict[str, str] | None = None) -> str:
    context = get_context(request)
    return render(
        "settings_profile.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()),
        devices=devices(context.unit_of_work(), session.user), telegram_linked=is_linked(context.unit_of_work(), session.user), notice=notice, errors=errors or {}, password_min=PASSWORD_MIN, password_max=PASSWORD_MAX,
    )
