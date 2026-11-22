"""Нативные формы настроек и профиля, под токеном устройства."""

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import client_ip, get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.api.v1_json import utc_iso
from pricewatch.application.accounts import create_invite
from pricewatch.application.app_auth import DeviceUser, issue_pairing_code
from pricewatch.application.check_settings import SETTING_KEYS, CheckForm, CheckSettings, check_settings, save_check_settings
from pricewatch.application.profile import change_device_password, devices, revoke_device
from pricewatch.application.stores_admin import COLORS, change_store, delete_store, set_store_enabled, stores_overview
from pricewatch.application.telegram import is_linked, issue_link_code, unlink
from pricewatch.application.users_admin import block_user, delete_invites, demote_admin, unblock_user, users_page
from pricewatch.domain.accounts import Role
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.schedule import TIMES_MAX

router = APIRouter(prefix="/api/v1")
EntityId = Annotated[int, Path(ge=1, le=2**62)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StoreForm(Body):
    name: Annotated[str, Field(max_length=200)]
    color: Annotated[str, Field(max_length=40)]


class StoreState(Body):
    enabled: bool


class ScheduleForm(Body):
    times: Annotated[list[Annotated[str, Field(max_length=5)]], Field(max_length=TIMES_MAX)]
    stale_after_days: Annotated[int, Field(ge=0, le=10_000)]
    title_similarity_percent: Annotated[int, Field(ge=0, le=100)]
    price_jump_percent: Annotated[int, Field(ge=0, le=10_000)]
    versions: Annotated[dict[str, Annotated[int, Field(ge=0, le=2**62)]], Field(max_length=4)]


class UserAction(Body):
    action: Literal["block", "unblock", "demote"]


class InviteForm(Body):
    role: Literal["user", "admin"]


class PasswordForm(Body):
    current_password: Annotated[str, Field(max_length=128)]
    password: Annotated[str, Field(max_length=128)]
    password_repeat: Annotated[str, Field(max_length=128)]


@router.get("/manage/stores")
def stores(request: Request, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    return {"items": [asdict(store) for store in stores_overview(context.unit_of_work(), auth.user)], "colors": COLORS}


@router.patch("/manage/stores/{store_id}")
def edit_store(request: Request, store_id: EntityId, body: StoreForm, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def save() -> dict:
        change_store(context.unit_of_work(), auth.user, store_id, name=body.name, color=body.color, now=context.clock())
        return {}

    return idempotent(request, auth.user.id, body.model_dump(), save)


@router.put("/manage/stores/{store_id}/enabled")
def enable_store(request: Request, store_id: EntityId, body: StoreState, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def save() -> dict:
        set_store_enabled(context.unit_of_work(), auth.user, store_id, body.enabled, context.clock())
        return {}

    return idempotent(request, auth.user.id, body.model_dump(), save)


@router.delete("/manage/stores/{store_id}")
def remove_store(request: Request, store_id: EntityId, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def remove() -> dict:
        delete_store(context.unit_of_work(), auth.user, store_id, context.clock())
        return {}

    return idempotent(request, auth.user.id, {}, remove)


def _schedule_json(settings: CheckSettings) -> dict:
    return {**asdict(settings), "times": [value.strftime("%H:%M") for value in settings.times],
            "next_run_at": utc_iso(settings.next_run_at)}


@router.get("/manage/schedule")
def schedule(request: Request, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    return _schedule_json(check_settings(context.unit_of_work(), auth.user, context.clock()))


@router.put("/manage/schedule")
def update_schedule(request: Request, body: ScheduleForm, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    if set(body.versions) != set(SETTING_KEYS):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"versions": "invalid"}})
    times = body.times + [""] * (TIMES_MAX - len(body.times))
    numbers = {"stale_after_days": str(body.stale_after_days), "title_similarity_percent": str(body.title_similarity_percent),
               "price_jump_percent": str(body.price_jump_percent)}
    return idempotent(request, auth.user.id, body.model_dump(), lambda: _schedule_json(
        save_check_settings(context.unit_of_work(), auth.user, CheckForm(times, numbers, body.versions), context.clock()),
    ))


@router.get("/manage/users")
def users(request: Request, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    return {**asdict(users_page(context.unit_of_work(), auth.user)), "own_id": auth.user.id}


@router.post("/manage/users/{user_id}")
def manage_user(request: Request, user_id: EntityId, body: UserAction, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def apply() -> dict:
        action = {"block": block_user, "unblock": unblock_user, "demote": demote_admin}[body.action]
        action(context.unit_of_work(), auth.user, user_id, context.clock())
        return {}

    return idempotent(request, auth.user.id, body.model_dump(), apply)


@router.post("/manage/invites", status_code=201)
def issue_invite(request: Request, body: InviteForm, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def issue() -> dict:
        invite = create_invite(context.unit_of_work(), context.secret_key, auth.user.id, Role(body.role), context.clock())
        return {"url": f"{request.app.state.public_origin}/web/register?invite={invite.code}", "expires_at": utc_iso(invite.invite.expires_at)}

    return idempotent(request, auth.user.id, body.model_dump(), issue, 201, one_time=True)


@router.delete("/manage/invites/{invite_id}")
def remove_invite(request: Request, invite_id: EntityId, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, {}, lambda: {
        "deleted": delete_invites(context.unit_of_work(), auth.user, [invite_id], context.clock()),
    })


@router.get("/profile")
def profile(request: Request, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    return {"devices": [asdict(device) for device in devices(context.unit_of_work(), auth.user)], "own_device_id": auth.device_id,
            "telegram_linked": is_linked(context.unit_of_work(), auth.user)}


@router.post("/profile/pairing-code")
def pairing_code(request: Request, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def issue() -> dict:
        result = issue_pairing_code(context.unit_of_work(), context.secret_key, auth.user.id, context.clock())
        return {"code": result.code, "expires_at": utc_iso(result.expires_at)}

    return idempotent(request, auth.user.id, {}, issue, one_time=True)


@router.post("/profile/telegram/code")
def telegram_code(request: Request, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def issue() -> dict:
        result = issue_link_code(context.unit_of_work(), context.secret_key, auth.user, context.clock())
        bot = request.app.state.telegram_bot
        return {"code": result.code, "expires_at": utc_iso(result.expires_at),
                "url": f"https://t.me/{bot}?start={result.code}" if bot else None}

    return idempotent(request, auth.user.id, {}, issue, one_time=True)


@router.delete("/profile/telegram")
def unlink_telegram(request: Request, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def remove() -> dict:
        unlink(context.unit_of_work(), auth.user, context.clock())
        return {}

    return idempotent(request, auth.user.id, {}, remove)


@router.delete("/profile/devices/{device_id}")
def remove_device(request: Request, device_id: EntityId, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def remove() -> dict:
        revoke_device(context.unit_of_work(), auth.user, device_id, context.clock())
        return {}

    return idempotent(request, auth.user.id, {}, remove)


@router.post("/auth/password", status_code=204)
def password(request: Request, body: PasswordForm, auth: DeviceUser = Depends(app_user)) -> Response:
    context = get_context(request)
    change_device_password(context.unit_of_work(), context.hasher, auth.user, current=body.current_password, new=body.password,
                           repeat=body.password_repeat, ip=client_ip(request), now=context.clock())
    return Response(status_code=204)
