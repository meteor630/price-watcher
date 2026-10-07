"""Настройки «Проверка» (администратор, SITE_DESIGN_SPEC.md, раздел 8): расписание, срок актуальности цены, пороги.

Ошибки показываются у полей, введённые значения сохраняются; конфликт версий — актуальные значения и сообщение.
Права проверяет application.check_settings (роль из базы).
"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.application.check_settings import NUMBER_FIELDS, SETTING_KEYS, CheckForm, CheckSettings, check_settings, save_check_settings
from pricewatch.application.dashboard import run_status
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.schedule import TIMES_MAX
from pricewatch.web.templates import render

router = APIRouter()

PAGE = "/web/settings/check"
TIME_FIELDS = [f"check_time_{index}" for index in range(1, TIMES_MAX + 1)]
NUMBER_NAMES = list(NUMBER_FIELDS)
FORM_FIELDS = [*TIME_FIELDS, *NUMBER_NAMES]
TIME_MESSAGES = {
    "required": "Укажите время ежедневной проверки.",
    "invalid": "Время — ЧЧ:ММ, от 00:00 до 23:59.",
    "duplicate": "Это время уже указано.",
}
RANGE_MESSAGES = {name: f"Целое число от {low} до {high}." for name, (_, (low, high)) in NUMBER_FIELDS.items()}


@router.get(PAGE)
def check_page(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    current = check_settings(context.unit_of_work(), session.user, context.clock())
    return HTMLResponse(_page(request, session, current, _values(current), {}, saved=False, flash=None))


@router.post(PAGE)
async def save_check(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    raw = await read_form(request)
    context = get_context(request)
    entered = {name: raw.get(name, "") for name in FORM_FIELDS}
    form = CheckForm(
        times=[entered[name] for name in TIME_FIELDS],
        numbers={name: entered[name] for name in NUMBER_NAMES},
        versions={key: _version(raw, f"version_{key}") for key in SETTING_KEYS},
    )
    try:
        saved = await run_in_threadpool(save_check_settings, context.unit_of_work(), session.user, form, context.clock())
    except PriceWatcherError as error:
        fields = error.details.get("fields") if error.code is ErrorCode.VALIDATION_ERROR else None
        # Ошибки полей формы — у полей; остальное (права, подделанная версия) — обычным ответом об ошибке.
        on_form = isinstance(fields, dict) and bool(fields) and all(name in entered for name in fields)
        if error.code is not ErrorCode.VERSION_CONFLICT and not on_form:
            raise
        current = await run_in_threadpool(check_settings, context.unit_of_work(), session.user, context.clock())
        if error.code is ErrorCode.VERSION_CONFLICT:
            return HTMLResponse(_page(request, session, current, _values(current), {}, saved=False, flash=SAFE_MESSAGES[error.code]))
        errors = {name: _message(name, reason) for name, reason in fields.items()}  # type: ignore[union-attr]
        return HTMLResponse(_page(request, session, current, entered, errors, saved=False, flash=None))
    return HTMLResponse(await run_in_threadpool(lambda: _page(request, session, saved, _values(saved), {}, saved=True, flash=None)))


def _message(name: str, reason: str) -> str:
    return RANGE_MESSAGES[name] if name in RANGE_MESSAGES else TIME_MESSAGES[reason]


def _page(
    request: Request, session: SessionUser, current: CheckSettings, values: dict[str, str], errors: dict[str, str], *, saved: bool, flash: str | None
) -> str:
    context = get_context(request)
    return render(
        "settings_check.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()),
        current=current, values=values, errors=errors, saved=saved, flash_message=flash, ranges={name: limits for name, (_, limits) in NUMBER_FIELDS.items()},
    )


def _values(current: CheckSettings) -> dict[str, str]:
    times = [at.strftime("%H:%M") for at in current.times]
    values = {name: times[index] if index < len(times) else "" for index, name in enumerate(TIME_FIELDS)}
    values["stale_after_days"] = str(current.stale_after_days)
    values["title_similarity_percent"] = str(current.title_similarity_percent)
    values["price_jump_percent"] = str(current.price_jump_percent)
    return values


def _version(form: dict[str, str], name: str) -> int:
    raw = form.get(name, "")
    if not raw.isascii() or not raw.isdigit() or int(raw) > 2**62:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {name: "invalid"}})
    return int(raw)
