"""Главная сайта (SITE_DESIGN_SPEC.md, раздел 4): страница и фрагмент списка сводной панели.

Смена периода — тот же адрес с `?period=…`: без JS это обычный переход, с htmx заменяются только цифры
и карточки, переключатель остаётся на месте и двигается плавно. Список панели приходит фрагментом
страницами по 20; «Показать ещё» дописывает следующую страницу.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.application.dashboard import Panel, dashboard, panel_items
from pricewatch.application.reviews import review_cards
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.days import Period
from pricewatch.web.templates import render

router = APIRouter()

PANEL_PAGE = 20
PANEL_TITLES = {
    Panel.TRACKED: "Отслеживаются",
    Panel.CHEAPER: "Подешевели",
    Panel.PRICIER: "Подорожали",
    Panel.OUT_OF_STOCK: "Нет в наличии",
}
PANEL_EMPTY = {
    Panel.TRACKED: "Товаров со ссылками пока нет.",
    Panel.CHEAPER: "Цены не менялись.",
    Panel.PRICIER: "Цены не менялись.",
    Panel.OUT_OF_STOCK: "Всё в наличии.",
}


@router.get("/web/")
def home_page(request: Request, period: Period = Period.DAY, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(home_html(request, session, period))


def home_html(
    request: Request, session: SessionUser, period: Period, *, flash: str | None = None, toast: str | None = None, undo_token: str | None = None
) -> str:
    """Главная целиком: её ответы htmx берут сводку, остальное и сообщение (решения, порядок карточек)."""
    context = get_context(request)
    board = dashboard(context.unit_of_work(), session.user, period, context.clock())
    reviews = review_cards(context.unit_of_work(), session.user, context.clock()) if board.reviews_pending else []
    return render(
        "home.html", session_user=session, board=board, header_run=board.run, panels=PANEL_TITLES, reviews=reviews,
        flash_message=flash, toast=toast, undo_token=undo_token,
    )


@router.get("/web/dashboard/items")
def panel_fragment(
    request: Request,
    panel: Panel,
    period: Period = Period.DAY,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    session: SessionUser = Depends(web_user),
) -> HTMLResponse:
    context = get_context(request)
    page = panel_items(context.unit_of_work(), session.user, panel, period, context.clock(), offset, PANEL_PAGE)
    html = render(
        "fragments/panel_items.html",
        panel=panel, title=PANEL_TITLES[panel], empty=PANEL_EMPTY[panel], period=period, page=page, first=offset == 0,
    )
    return HTMLResponse(html)
