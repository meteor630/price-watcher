"""Настройки «Проверка» (администратор, SITE_DESIGN_SPEC.md, раздел 8): расписание и срок актуальности цены.

Ошибки показываются у полей, введённые значения сохраняются; конфликт версий — актуальные значения и сообщение.
Права проверяет application.check_settings (роль из базы).
"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.application.check_settings import CheckSettings, check_settings, save_check_settings
from pricewatch.application.dashboard import run_status
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import STALE_AFTER_DAYS_MAX, STALE_AFTER_DAYS_MIN
from pricewatch.domain.schedule import TIMES_MAX
from pricewatch.web.templates import render

router = APIRouter()

PAGE = "/web/settings/check"
TIME_FIELDS = [f"check_time_{index}" for index in range(1, TIMES_MAX + 1)]
FIELD_MESSAGES = {
    "required": "Укажите время ежедневной проверки.",
    "invalid": "Время — ЧЧ:ММ, от 00:00 до 23:59.",
    "duplicate": "Это время уже указано.",
    "out_of_range": f"Целое число от {STALE_AFTER_DAYS_MIN} до {STALE_AFTER_DAYS_MAX}.",
}


@router.get(PAGE)
def check_page(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    current = check_settings(context.unit_of_work(), session.user, context.clock())
    return HTMLResponse(_page(request, session, current, _values(current), {}, saved=False, flash=None))


@router.post(PAGE)
async def save_check(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    entered = {name: form.get(name, "") for name in [*TIME_FIELDS, "stale_after_days"]}

    def save() -> CheckSettings:
        return save_check_settings(
            context.unit_of_work(), session.user, times=[entered[name] for name in TIME_FIELDS], stale_after_days=entered["stale_after_days"],
            times_version=_version(form, "times_version"), stale_version=_version(form, "stale_version"), now=context.clock(),
        )

    try:
        saved = await run_in_threadpool(save)
    except PriceWatcherError as error:
        fields = error.details.get("fields") if error.code is ErrorCode.VALIDATION_ERROR else None
        # Ошибки полей формы — у полей; остальное (права, подделанная версия) — обычным ответом об ошибке.
        on_form = isinstance(fields, dict) and bool(fields) and all(name in entered and reason in FIELD_MESSAGES for name, reason in fields.items())
        if error.code is not ErrorCode.VERSION_CONFLICT and not on_form:
            raise
        current = await run_in_threadpool(check_settings, context.unit_of_work(), session.user, context.clock())
        if error.code is ErrorCode.VERSION_CONFLICT:
            return HTMLResponse(_page(request, session, current, _values(current), {}, saved=False, flash=SAFE_MESSAGES[error.code]))
        errors = {name: FIELD_MESSAGES[reason] for name, reason in fields.items()}  # type: ignore[union-attr]
        return HTMLResponse(_page(request, session, current, entered, errors, saved=False, flash=None))
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, saved, _values(saved), {}, saved=True, flash=None)))


def _page(
    request: Request, session: SessionUser, current: CheckSettings, values: dict[str, str], errors: dict[str, str], *, saved: bool, flash: str | None
) -> str:
    context = get_context(request)
    return render(
        "settings_check.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()),
        current=current, values=values, errors=errors, saved=saved, flash_message=flash, stale_min=STALE_AFTER_DAYS_MIN, stale_max=STALE_AFTER_DAYS_MAX,
    )


def _values(current: CheckSettings) -> dict[str, str]:
    times = [at.strftime("%H:%M") for at in current.times]
    values = {name: times[index] if index < len(times) else "" for index, name in enumerate(TIME_FIELDS)}
    values["stale_after_days"] = str(current.stale_after_days)
    return values


def _version(form: dict[str, str], name: str) -> int:
    raw = form.get(name, "")
    if not raw.isascii() or not raw.isdigit() or int(raw) > 2**62:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {name: "invalid"}})
    return int(raw)
