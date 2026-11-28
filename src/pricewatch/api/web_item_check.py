"""Кнопка «Проверить цену» на сайте: POST ставит проверку всех ссылок товара, GET отдаёт её состояние (для плитки в списке;
карточка товара во время проверки опрашивает саму страницу — так обновляются и цены).

Только владелец товара (чужой — 404), изменяющий запрос — с Origin и CSRF (web_user). Ошибки показываются во фрагменте
понятным текстом: лимит частоты с временем до следующей попытки, нет ссылок для проверки. Причина неудачной проверки
магазина (не ответил, цена не найдена, домен не разрешён) — в карточке товара после обновления, как и раньше.
"""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.application.manual_checks import ItemCheckState, check_item, item_check_state
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import format_rub
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
Compact = Annotated[int, Query(ge=0, le=1)]


def _fragment(item_id: int, compact: int, state: ItemCheckState | None, *, problem: str | None = None, done: bool = False) -> str:
    price = state.best_price_minor if state is not None else None
    return render(
        "fragments/item_check.html", item_id=item_id, compact=bool(compact), state=state, problem=problem, done=done,
        price_text=format_rub(price) if price is not None else None,
    )


def _problem(error: PriceWatcherError) -> str:
    if error.code is ErrorCode.RATE_LIMITED:
        seconds = error.details.get("retry_after_seconds")
        minutes = max(1, math.ceil(seconds / 60)) if isinstance(seconds, int) else 1
        return f"Слишком много проверок подряд. Следующая — через {minutes} мин."
    if error.code is ErrorCode.VALIDATION_ERROR:
        return "У товара нет ссылок, которые можно проверить: добавьте ссылку или включите магазин."
    raise error


@router.post("/web/items/{item_id}/check")
async def check_now(request: Request, item_id: EntityId, compact: Compact = 0, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    try:
        await run_in_threadpool(check_item, context.unit_of_work(), session.user, item_id, context.clock())
    except PriceWatcherError as error:
        problem = _problem(error)
        state = await run_in_threadpool(item_check_state, context.unit_of_work(), session.user, item_id, context.clock())
        return HTMLResponse(_fragment(item_id, compact, state, problem=problem))
    state = await run_in_threadpool(item_check_state, context.unit_of_work(), session.user, item_id, context.clock())
    return HTMLResponse(_fragment(item_id, compact, state))


@router.get("/web/items/{item_id}/check")
def check_state(request: Request, item_id: EntityId, compact: Compact = 0, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    state = item_check_state(context.unit_of_work(), session.user, item_id, context.clock())
    return HTMLResponse(_fragment(item_id, compact, state, done=not state.checking))
