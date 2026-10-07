"""Карточка товара на сайте и изменение количества и отметки «в итоге» (SITE_DESIGN_SPEC.md, разделы 5 и 6).

«Куплено», «В архив» и «Вернуть в список» (раздел 6) — там же: форма покупки и подтверждение возврата
купленного товара открываются окном; после покупки и архива — сообщение с «Отменить».

Изменение — htmx-запрос с CSRF (общая проверка /web) и версией записи. Ответ — обновлённая страница,
из которой htmx берёт сводку (#hero-figures), остальное (#page-rest) и сообщение (#flash): итоги
пересчитываются за один запрос. Конфликт версий не теряет страницу: показаны актуальные данные
и сообщение «Запись уже изменили…».
"""

import re
from collections.abc import Callable
from datetime import date
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
from pricewatch.application.purchases import archive_item, purchase_form, purchase_item, return_to_list
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.money import parse_rub
from pricewatch.domain.shopping import ItemState
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
VIEWS = frozenset({"item", "category", "settings"})
DAY_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


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


@router.get("/web/items/{item_id}/purchase")
def purchase_dialog(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    return HTMLResponse(render("fragments/purchase_form.html", form=purchase_form(context.unit_of_work(), session.user, item_id, context.clock())))


@router.post("/web/items/{item_id}/purchase")
async def item_purchase(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    fields: dict[str, str] = {}
    version = _int(form.get("version"), 1, 2**62, "version", fields)
    quantity = _int(form.get("quantity"), 1, 999, "purchase_quantity", fields)
    raw_store = form.get("store_id", "")
    store_id = None if raw_store == "" else _int(raw_store, 1, 2**62, "purchase_store", fields)
    if fields:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": fields})
    day = _day(form.get("day", ""))
    price = parse_rub(form.get("price", ""), "purchase_price")
    context = get_context(request)

    def run() -> str:
        return purchase_item(
            context.unit_of_work(), context.secret_key, session.user, item_id, version, price_minor=price, quantity=quantity, store_id=store_id,
            day=day, now=context.clock(),
        )

    return await _state_change(request, session, item_id, run, "Отмечено как купленное.")


@router.post("/web/items/{item_id}/archive")
async def item_archive(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    version = _version(form)
    context = get_context(request)
    return await _state_change(
        request, session, item_id, lambda: archive_item(context.unit_of_work(), context.secret_key, session.user, item_id, version, context.clock()),
        "Товар перенесён в архив.",
    )


@router.get("/web/items/{item_id}/return")
def return_dialog(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    """Подтверждение нужно купленному товару: возврат стирает покупку. Архивный возвращается сразу."""
    context = get_context(request)
    detail = item_detail(context.unit_of_work(), session.user, item_id, context.clock())
    if detail.state is not ItemState.PURCHASED:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"state": "invalid_transition"}})
    return HTMLResponse(render("fragments/return_purchase.html", detail=detail))


@router.post("/web/items/{item_id}/return")
async def item_return(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    version = _version(form)
    context = get_context(request)
    return await _state_change(request, session, item_id, lambda: return_to_list(context.unit_of_work(), session.user, item_id, version), None)


async def _state_change(request: Request, session: SessionUser, item_id: int, action: Callable[[], object], done: str | None) -> HTMLResponse:
    """Страница товара после перехода; токен «Отменить» — если действие его вернуло, конфликт версий — сообщением."""
    flash = None
    toast = token = None
    try:
        result = await run_in_threadpool(action)
        if done is not None and isinstance(result, str):
            toast, token = done, result
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    return HTMLResponse(await run_in_threadpool(lambda: _item_page(request, session, item_id, flash, toast=toast, undo_token=token)))


def _version(form: dict[str, str]) -> int:
    fields: dict[str, str] = {}
    version = _int(form.get("version"), 1, 2**62, "version", fields)
    if fields:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": fields})
    return version


def _day(raw: str) -> date:
    """Дата поля type=date: только ГГГГ-ММ-ДД (fromisoformat понимает и другие записи)."""
    invalid = PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"purchased_at": "invalid"}})
    if DAY_PATTERN.fullmatch(raw) is None:
        raise invalid
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise invalid from error


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


def _item_page(request: Request, session: SessionUser, item_id: int, flash: str | None, *, toast: str | None = None, undo_token: str | None = None) -> str:
    context = get_context(request)
    now = context.clock()
    detail = item_detail(context.unit_of_work(), session.user, item_id, now)
    store_id = detail.best_store.store_id if detail.best_store else (detail.chart_stores[0].store_id if detail.chart_stores else None)
    points = [] if store_id is None else item_chart(context.unit_of_work(), session.user, item_id, store_id, ChartRange.MONTH, now)
    return render(
        "item.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user), detail=detail,
        store_id=store_id, chart_range=ChartRange.MONTH, points=points, flash_message=flash, toast=toast, undo_token=undo_token,
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
