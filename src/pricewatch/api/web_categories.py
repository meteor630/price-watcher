"""Страницы категории и группы на сайте (SITE_DESIGN_SPEC.md, разделы 5 и 7).

Период — тот же адрес с `?period=…` (htmx заменяет цифры и таблицу, переключатель двигается плавно).
Диапазон графика — фрагмент `/web/categories/{id}/chart?range=…`. Чужая категория или группа — 404.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.application.category import category_chart, category_detail, group_detail
from pricewatch.application.dashboard import run_status
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]


@router.get("/web/categories/{category_id}")
def category_page(request: Request, category_id: EntityId, period: Period = Period.DAY, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    now = context.clock()
    detail = category_detail(context.unit_of_work(), session.user, category_id, period, now)
    points = category_chart(context.unit_of_work(), session.user, category_id, ChartRange.MONTH, now)
    run = run_status(context.unit_of_work(), session.user)
    return HTMLResponse(render("category.html", session_user=session, header_run=run, detail=detail, points=points, chart_range=ChartRange.MONTH))


@router.get("/web/categories/{category_id}/chart")
def category_chart_fragment(
    request: Request, category_id: EntityId, range: ChartRange = ChartRange.MONTH, session: SessionUser = Depends(web_user)  # noqa: A002 — как в API
) -> HTMLResponse:
    context = get_context(request)
    points = category_chart(context.unit_of_work(), session.user, category_id, range, context.clock())
    return HTMLResponse(render("fragments/category_chart.html", category_id=category_id, points=points, chart_range=range))


@router.get("/web/groups/{group_id}")
def group_page(request: Request, group_id: EntityId, period: Period = Period.DAY, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    detail = group_detail(context.unit_of_work(), session.user, group_id, period, context.clock())
    return HTMLResponse(render("group.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user), detail=detail))

