"""«Требует решения» на главной: принять, отклонить, перепроверить сейчас (DEVELOPMENT_ARCHITECTURE.md, раздел 6).

Каждый решает только за свои товары. Ответ — главная целиком (htmx берёт остальное, сводку и сообщение):
решение сразу меняет итоги. После действия — сообщение с «Отменить». Карточка, по которой уже решили
(вторая вкладка, двойное нажатие), — NOT_FOUND; одновременная запись двух решений — «Запись уже изменили…».
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.api.web_home import home_html
from pricewatch.application.reviews import Recheck, decide, recheck
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.days import Period
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.review import Decision

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]


@router.post("/web/reviews/{review_id}/accept")
async def accept(request: Request, review_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    return await _act(request, session, lambda: (decide(context.unit_of_work(), context.secret_key, session.user, review_id, Decision.ACCEPT, context.clock()), "Новое значение принято."))


@router.post("/web/reviews/{review_id}/reject")
async def reject(request: Request, review_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    return await _act(request, session, lambda: (decide(context.unit_of_work(), context.secret_key, session.user, review_id, Decision.REJECT, context.clock()), "Изменение отклонено."))


@router.post("/web/reviews/{review_id}/recheck")
async def recheck_now(request: Request, review_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)

    def run() -> tuple[str | None, str]:
        result: Recheck = recheck(context.unit_of_work(), context.secret_key, session.user, review_id, context.clock())
        if result.undo_token is None:
            return None, "Эта ссылка уже проверяется."
        return result.undo_token, "Перепроверка поставлена — цена обновится после проверки."

    return await _act(request, session, run)


async def _act(request: Request, session: SessionUser, action: Callable[[], tuple[str | None, str]]) -> HTMLResponse:
    form = await read_form(request)
    try:
        period = Period(form.get("period", "day"))
    except ValueError as error:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"period": "invalid"}}) from error
    flash = toast = token = None
    try:
        token, toast = await run_in_threadpool(action)
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    return HTMLResponse(await run_in_threadpool(lambda: home_html(request, session, period, flash=flash, toast=toast, undo_token=token)))
