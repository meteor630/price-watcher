"""Ошибки на сайте: HTML вместо JSON (SITE_DESIGN_SPEC.md, разделы 11 и 14).

- Нет сессии: обычный переход ведёт на страницу входа, запрос htmx получает HX-Redirect туда же.
- Остальное: страница ошибки с безопасным текстом и номером ошибки; для htmx — фрагмент
  в общую полосу сообщений страницы (#flash), он заменяет её содержимое.
"""

from collections.abc import Mapping

from starlette.responses import HTMLResponse, RedirectResponse, Response

from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode
from pricewatch.web.templates import render

WEB_PREFIX = "/web"
LOGIN_PATH = "/web/login"


def is_web_path(path: str) -> bool:
    return path == "/" or path == WEB_PREFIX or path.startswith(WEB_PREFIX + "/")


def web_error_response(
    code: ErrorCode, status: int, *, htmx: bool, error_id: str | None = None, details: Mapping[str, object] | None = None
) -> Response:
    if code is ErrorCode.AUTH_REQUIRED:
        if htmx:
            return Response(status_code=401, headers={"HX-Redirect": LOGIN_PATH})
        return RedirectResponse(LOGIN_PATH, status_code=303)
    message = SAFE_MESSAGES[code]
    headers: dict[str, str] = {}
    retry_after = (details or {}).get("retry_after_seconds")
    if isinstance(retry_after, int):
        headers["Retry-After"] = str(retry_after)
    if htmx:
        fragment = render("fragments/flash.html", code=code.value, message=message, error_id=error_id)
        return HTMLResponse(fragment, status_code=status, headers=headers | {"HX-Retarget": "#flash", "HX-Reswap": "innerHTML"})
    page = render("error.html", code=code.value, status=status, message=message, error_id=error_id)
    return HTMLResponse(page, status_code=status, headers=headers)
