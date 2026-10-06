"""Карточка товара на сайте и изменение количества и отметки «в итоге» (SITE_DESIGN_SPEC.md, разделы 5 и 6).

Изменение — htmx-запрос с CSRF (общая проверка /web) и версией записи. Ответ — обновлённая страница,
из которой htmx берёт сводку (#hero-figures), остальное (#page-rest) и сообщение (#flash): итоги
пересчитываются за один запрос. Конфликт версий не теряет страницу: показаны актуальные данные
и сообщение «Запись уже изменили…».
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.api.web_settings_items import settings_items_page
from pricewatch.application.category import category_chart, category_detail
from pricewatch.application.dashboard import run_status
from pricewatch.application.item import item_chart, item_detail, update_item_settings
from pricewatch.application.ports import UNCHANGED
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
VIEWS = frozenset({"item", "category", "settings"})


@router.get("/web/items/{item_id}")
def item_page(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(_item_page(request, session, item_id, flash=None))


@router.get("/web/items/{item_id}/chart")
def item_chart_fragment(
    request: Request,
    item_id: EntityId,
    store_id: Annotated[int, Query(ge=1, le=2**62)],
    range: ChartRange = ChartRange.MONTH,  # noqa: A002 — как в API
    session: SessionUser = Depends(web_user),
) -> HTMLResponse:
    context = get_context(request)
    detail = item_detail(context.unit_of_work(), session.user, item_id, context.clock())
    points = item_chart(context.unit_of_work(), session.user, item_id, store_id, range, context.clock())
    return HTMLResponse(render("fragments/item_chart.html", detail=detail, store_id=store_id, chart_range=range, points=points))


@router.post("/web/items/{item_id}/settings")
async def item_settings(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    view = form.get("view", "item")
    version, quantity, in_total, period = _parse(form, view)
    context = get_context(request)
    flash = None
    try:
        await run_in_threadpool(
            update_item_settings, context.unit_of_work(), session.user, item_id, version, context.clock(), quantity=quantity, in_total=in_total
        )
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    if view == "category":
        detail = await run_in_threadpool(item_detail, context.unit_of_work(), session.user, item_id, context.clock())
        html = await run_in_threadpool(_category_page, request, session, detail.category_id, period, flash)
    elif view == "settings":
        raw_filter = form.get("filter", "")
        html = await run_in_threadpool(settings_items_page, request, session, int(raw_filter) if raw_filter.isdigit() else None, flash)
    else:
        html = await run_in_threadpool(_item_page, request, session, item_id, flash)
    return HTMLResponse(html)


def _parse(form: dict[str, str], view: str) -> tuple[int, object, object, Period]:
    fields: dict[str, str] = {}
    if view not in VIEWS:
        fields["view"] = "invalid"
    version = _int(form.get("version"), 1, 2**62, "version", fields)
    quantity: object = UNCHANGED
    if "quantity" in form:
        quantity = _int(form["quantity"], 1, 999, "quantity", fields)
    in_total: object = UNCHANGED
    if "in_total" in form:
        if form["in_total"] not in ("true", "false"):
            fields["in_total"] = "invalid"
        in_total = form["in_total"] == "true"
    try:
        period = Period(form.get("period", "day"))
    except ValueError:
        fields["period"] = "invalid"
        period = Period.DAY
    if fields:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": fields})
    return version, quantity, in_total, period


def _int(raw: str | None, low: int, high: int, name: str, fields: dict[str, str]) -> int:
    if raw is None or not raw.isdigit() or not low <= int(raw) <= high:
        fields[name] = "invalid"
        return low
    return int(raw)


def _item_page(request: Request, session: SessionUser, item_id: int, flash: str | None) -> str:
    context = get_context(request)
    now = context.clock()
    detail = item_detail(context.unit_of_work(), session.user, item_id, now)
    store_id = detail.best_store.store_id if detail.best_store else (detail.chart_stores[0].store_id if detail.chart_stores else None)
    points = [] if store_id is None else item_chart(context.unit_of_work(), session.user, item_id, store_id, ChartRange.MONTH, now)
    return render(
        "item.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user), detail=detail,
        store_id=store_id, chart_range=ChartRange.MONTH, points=points, flash_message=flash,
    )


def _category_page(request: Request, session: SessionUser, category_id: int, period: Period, flash: str | None) -> str:
    context = get_context(request)
    now = context.clock()
    detail = category_detail(context.unit_of_work(), session.user, category_id, period, now)
    points = category_chart(context.unit_of_work(), session.user, category_id, ChartRange.MONTH, now)
    return render(
        "category.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user), detail=detail,
        points=points, chart_range=ChartRange.MONTH, flash_message=flash,
    )
