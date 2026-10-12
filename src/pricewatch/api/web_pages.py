"""Страницы входа, регистрации и выхода (SITE_DESIGN_SPEC.md, раздел 14; DEVELOPMENT_ARCHITECTURE.md, раздел 4).

- Вход и регистрация работают обычной формой, без JavaScript. Сессии ещё нет, поэтому от подделки
  запроса с чужого сайта защищает совпадение Origin с адресом сайта.
- Ошибка входа не уточняет, что неверно; после превышения попыток показывается время ожидания.
- Регистрация — только по действующему приглашению (`?invite=…`: строка запроса в журнал не попадает).
  Недействительное приглашение — объяснение без формы. Неверные поля не расходуют приглашение.
- Выход — запрос htmx с CSRF-токеном (общая проверка /web), затем переход на страницу входа.
"""

from collections.abc import Mapping

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, RedirectResponse, Response

from pricewatch.api.context import client_ip, get_context
from pricewatch.api.web_auth import SESSION_COOKIE, clear_session_cookie, set_session_cookie, web_user
from pricewatch.api.web_errors import LOGIN_PATH
from pricewatch.api.web_forms import read_form
from pricewatch.application.accounts import invite_is_valid, register
from pricewatch.application.web_auth import SessionUser, authenticate_session, login, logout
from pricewatch.domain.accounts import PASSWORD_MAX, PASSWORD_MIN
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

HOME_PATH = "/web/"
LOGIN_CHECK_PATH = "/web/login/check"
COOKIE_MISSING = (
    "Пароль верный, но браузер не сохранил вход. Разрешите cookie для этого сайта; "
    "если адрес начинается с http://, откройте его по https://."
)

FIELD_MESSAGES: Mapping[tuple[str, str], str] = {
    ("login", "format"): "3–64 символа: латиница, цифры, точка, дефис, подчёркивание.",
    ("password", "too_short"): f"Пароль не короче {PASSWORD_MIN} символов.",
    ("password", "too_long"): f"Пароль не длиннее {PASSWORD_MAX} символов.",
    ("password", "same_as_login"): "Пароль не должен совпадать с логином.",
    ("password_repeat", "mismatch"): "Пароли не совпадают.",
}


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(HOME_PATH, status_code=302)


@router.get("/web/login")
def login_page(request: Request, registered: str | None = None, cookie: str | None = None) -> Response:
    if _signed_in(request):
        return RedirectResponse(HOME_PATH, status_code=303)
    if cookie == "missing":
        return _login_page(error=COOKIE_MISSING)
    notice = "Регистрация завершена. Войдите со своим логином и паролем." if registered == "1" else None
    return _login_page(notice=notice)


@router.get(LOGIN_CHECK_PATH)
def login_check(request: Request) -> RedirectResponse:
    """После верного пароля: браузер прислал cookie сессии — на главную, нет — на вход с объяснением.
    Иначе Safari по http:// или запрет cookie молча возвращали бы на форму входа."""
    if _signed_in(request):
        return RedirectResponse(HOME_PATH, status_code=303)
    return RedirectResponse("/web/login?cookie=missing", status_code=303)


@router.post("/web/login")
async def login_submit(request: Request) -> Response:
    _require_same_origin(request)
    form = await read_form(request)
    login_value = form.get("login", "")
    context = get_context(request)
    try:
        issued = await run_in_threadpool(
            login, context.unit_of_work(), context.hasher, context.secret_key, login_value, form.get("password", ""),
            client_ip(request), context.clock(),
        )
    except PriceWatcherError as error:
        if error.code is ErrorCode.INVALID_CREDENTIALS:
            return _login_page(login_value=login_value, error=SAFE_MESSAGES[error.code], status=401)
        if error.code is ErrorCode.RATE_LIMITED:
            seconds = int(error.details["retry_after_seconds"])  # type: ignore[call-overload]
            minutes = max(1, -(-seconds // 60))
            message = f"Слишком много попыток. Попробуйте снова через {minutes} мин."
            response = _login_page(login_value=login_value, error=message, status=429)
            response.headers["Retry-After"] = str(seconds)
            return response
        raise
    response = RedirectResponse(LOGIN_CHECK_PATH, status_code=303)
    set_session_cookie(response, issued.token)
    return response


@router.get("/web/register")
def register_page(request: Request, invite: str = "") -> Response:
    context = get_context(request)
    if not invite_is_valid(context.unit_of_work(), context.secret_key, invite, context.clock()):
        return _invite_invalid_page()
    return _register_page(invite=invite)


@router.post("/web/register")
async def register_submit(request: Request) -> Response:
    _require_same_origin(request)
    form = await read_form(request)
    invite, login_value = form.get("invite", ""), form.get("login", "")
    password = form.get("password", "")
    if password != form.get("password_repeat", ""):
        return _register_page(invite=invite, login_value=login_value, errors={"password_repeat": FIELD_MESSAGES[("password_repeat", "mismatch")]})
    context = get_context(request)
    try:
        await run_in_threadpool(
            register, context.unit_of_work(), context.hasher, context.secret_key, invite, login_value, password, login_value.strip(),
            context.clock(),
        )
    except PriceWatcherError as error:
        if error.code is ErrorCode.INVITE_INVALID:
            return _invite_invalid_page()
        if error.code is ErrorCode.LOGIN_TAKEN:
            return _register_page(invite=invite, login_value=login_value, errors={"login": SAFE_MESSAGES[error.code]}, status=409)
        if error.code is ErrorCode.VALIDATION_ERROR:
            fields = error.details.get("fields", {})
            errors = {field: FIELD_MESSAGES.get((field, str(reason)), "Неверное значение.") for field, reason in fields.items()}  # type: ignore[union-attr]
            return _register_page(invite=invite, login_value=login_value, errors=errors, status=422)
        raise
    return RedirectResponse(f"{LOGIN_PATH}?registered=1", status_code=303)


@router.post("/web/logout")
def logout_submit(request: Request, _: SessionUser = Depends(web_user)) -> Response:
    context = get_context(request)
    logout(context.unit_of_work(), context.secret_key, request.cookies.get(SESSION_COOKIE), context.clock())
    response = Response(status_code=204, headers={"HX-Redirect": LOGIN_PATH})
    clear_session_cookie(response)
    return response


def _signed_in(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    context = get_context(request)
    try:
        authenticate_session(context.unit_of_work(), context.secret_key, token, context.clock())
    except PriceWatcherError as error:
        if error.code is ErrorCode.AUTH_REQUIRED:
            return False  # сессия истекла или отозвана — показываем форму входа
        raise
    return True


def _require_same_origin(request: Request) -> None:
    # Сессии ещё нет, CSRF-токена тоже: форму с чужого сайта отличает только Origin.
    if request.headers.get("origin") != request.app.state.public_origin:
        raise PriceWatcherError(ErrorCode.CSRF_FAILED)


def _login_page(*, login_value: str = "", error: str | None = None, notice: str | None = None, status: int = 200) -> HTMLResponse:
    return HTMLResponse(render("login.html", login_value=login_value, error=error, notice=notice), status_code=status)


def _register_page(*, invite: str, login_value: str = "", errors: Mapping[str, str] | None = None, status: int = 200) -> HTMLResponse:
    html = render(
        "register.html", invite=invite, login_value=login_value, errors=errors or {}, password_min=PASSWORD_MIN, password_max=PASSWORD_MAX,
    )
    return HTMLResponse(html, status_code=status)


def _invite_invalid_page() -> HTMLResponse:
    return HTMLResponse(render("invite_invalid.html", message=SAFE_MESSAGES[ErrorCode.INVITE_INVALID]), status_code=400)
