"""Страница «Покупки» и вкладка «Архив» (SITE_DESIGN_SPEC.md, раздел 6а).

Период переключается без перезагрузки: htmx берёт из ответа сводку (#hero-figures) и остальное (#page-rest),
по умолчанию — месяц. В архиве «Вернуть в список» отвечает обновлённым архивом (web_items, view=archive).
"""

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.application.dashboard import run_status
from pricewatch.application.purchases import archive_list, purchases_overview
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.days import Period
from pricewatch.web.templates import render

router = APIRouter()


@router.get("/web/purchases")
def purchases_page(request: Request, period: Period = Period.MONTH, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    overview = purchases_overview(context.unit_of_work(), session.user, period, context.clock())
    return HTMLResponse(render("purchases.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()), overview=overview))


@router.get("/web/purchases/archive")
def archive_page(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(archive_html(request, session, flash=None))


def archive_html(request: Request, session: SessionUser, flash: str | None) -> str:
    context = get_context(request)
    entries = archive_list(context.unit_of_work(), session.user, context.clock())
    return render("archive.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()), entries=entries, flash_message=flash)
