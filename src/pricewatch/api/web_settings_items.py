"""Настройки сайта: товары (SITE_DESIGN_SPEC.md, раздел 8).

- Список товаров «в списке» с фильтром по категории; строка: «в итоге», имя, категория и группа, магазины,
  «выше/ниже» внутри своей группы, «Изменить», «Удалить» (с «Отменить»).
- Окно товара: ссылки, имя, полное имя, категория, группа, количество. Новый товар после сохранения
  остаётся в окне уже в режиме изменения: видно состояние каждой ссылки и ошибки тех, что не добавились.
- Ссылки в окне добавляются и снимаются по одной; пока какая-то проверяется, список обновляется сам.
Все изменения — htmx с CSRF (общая проверка /web); ответ — обновлённая страница или фрагмент.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from pricewatch.api.context import get_context
from pricewatch.api.web_auth import web_user
from pricewatch.api.web_forms import read_form
from pricewatch.application.category import items_overview
from pricewatch.application.dashboard import run_status
from pricewatch.application.shopping import (
    ItemForm, LinkAdded, add_links, create_item, delete_item, item_form, item_links_view, remove_link, reorder_items, update_item,
)
from pricewatch.application.web_auth import SessionUser
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.web.templates import render

router = APIRouter()

EntityId = Annotated[int, Path(ge=1, le=2**62)]
ITEMS_PATH = "/web/settings/items"
LINKS_MAX = 10


@router.get(ITEMS_PATH)
def items_page(request: Request, category: int | None = None, edit: int | None = None, new: int | None = None,
               session: SessionUser = Depends(web_user)) -> HTMLResponse:
    dialog = None
    if edit is not None:
        dialog = _form(request, session, edit, None)
    elif new is not None:
        dialog = _form(request, session, None, category)
    return HTMLResponse(settings_items_page(request, session, category, dialog=dialog))


@router.get("/web/settings/items/new")
def new_item_dialog(request: Request, category: int | None = None, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(render("fragments/item_form.html", form=_form(request, session, None, category), results=[]))


@router.get("/web/items/{item_id}/edit")
def edit_item_dialog(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    return HTMLResponse(render("fragments/item_form.html", form=_form(request, session, item_id, None), results=[]))


@router.get("/web/settings/group-options")
def group_options(request: Request, category_id: int, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = item_form(get_context(request).unit_of_work(), session.user, None, category_id)
    return HTMLResponse(render("fragments/group_select.html", groups=form.groups, selected=None))


@router.post(ITEMS_PATH)
async def item_create(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    fields = _item_fields(form)
    urls = [line for line in form.get("links", "").splitlines() if line.strip()]
    if len(urls) > LINKS_MAX:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"links": "too_many"}})
    context = get_context(request)
    item = await run_in_threadpool(create_item, context.unit_of_work(), session.user, fields.category_id, fields.group_id,
                                   fields.name, fields.full_name, fields.quantity)
    results = await run_in_threadpool(add_links, context.unit_of_work, context.links, session.user, item.id, urls, context.clock())
    dialog = await run_in_threadpool(_form, request, session, item.id, None)
    return HTMLResponse(await run_in_threadpool(lambda: settings_items_page(request, session, _filter(form), dialog=dialog, link_results=results)))


@router.post("/web/items/{item_id}/edit")
async def item_update(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    fields = _item_fields(form)
    context = get_context(request)
    flash = None
    try:
        await run_in_threadpool(
            lambda: update_item(
                context.unit_of_work(), session.user, item_id, _number(form, "version", 1, 2**62), name=fields.name, full_name=fields.full_name,
                quantity=fields.quantity, category_id=fields.category_id, group_id=fields.group_id,
            )
        )
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    return HTMLResponse(await run_in_threadpool(settings_items_page, request, session, _filter(form), flash))


@router.post("/web/items/{item_id}/delete")
async def item_delete(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    flash = toast = token = None
    try:
        token = await run_in_threadpool(delete_item, context.unit_of_work(), context.secret_key, session.user, item_id, _number(form, "version", 1, 2**62), context.clock())
        toast = "Товар удалён."
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    return HTMLResponse(await run_in_threadpool(lambda: settings_items_page(request, session, _filter(form), flash, toast=toast, undo_token=token)))


@router.post("/web/items/order")
async def items_order(request: Request, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    raw_group = form.get("group_id", "")
    group_id = None if raw_group == "" else _number(form, "group_id", 1, 2**62)
    ids_raw = form.get("ids", "").split(",")
    if not ids_raw or not all(part.isdigit() for part in ids_raw) or len(set(ids_raw)) != len(ids_raw):
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"ids": "invalid"}})
    flash = None
    try:
        await run_in_threadpool(reorder_items, context.unit_of_work(), session.user, _number(form, "category_id", 1, 2**62), group_id,
                                [int(part) for part in ids_raw], _number(form, "version", 1, 2**62))
    except PriceWatcherError as error:
        if error.code is not ErrorCode.VERSION_CONFLICT:
            raise
        flash = SAFE_MESSAGES[error.code]
    return HTMLResponse(await run_in_threadpool(settings_items_page, request, session, _filter(form), flash))


@router.get("/web/items/{item_id}/links")
def item_links_fragment(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    links = item_links_view(get_context(request).unit_of_work(), session.user, item_id)
    return HTMLResponse(render("fragments/item_links.html", item_id=item_id, links=links, results=[]))


@router.post("/web/items/{item_id}/links")
async def item_link_add(request: Request, item_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    form = await read_form(request)
    context = get_context(request)
    results = await run_in_threadpool(add_links, context.unit_of_work, context.links, session.user, item_id, [form.get("url", "")], context.clock())
    links = await run_in_threadpool(item_links_view, context.unit_of_work(), session.user, item_id)
    return HTMLResponse(render("fragments/item_links.html", item_id=item_id, links=links, results=results))


@router.post("/web/items/{item_id}/links/{item_link_id}/remove")
async def item_link_remove(request: Request, item_id: EntityId, item_link_id: EntityId, session: SessionUser = Depends(web_user)) -> HTMLResponse:
    context = get_context(request)
    token = await run_in_threadpool(remove_link, context.unit_of_work(), context.secret_key, session.user, item_id, item_link_id, context.clock())
    links = await run_in_threadpool(item_links_view, context.unit_of_work(), session.user, item_id)
    return HTMLResponse(render("fragments/item_links.html", item_id=item_id, links=links, results=[], toast="Ссылка снята.", undo_token=token))


def settings_items_page(
    request: Request, session: SessionUser, category: int | None, flash: str | None = None, *, dialog: ItemForm | None = None,
    link_results: list[LinkAdded] | None = None, toast: str | None = None, undo_token: str | None = None,
) -> str:
    """Страница «Товары»; `dialog` — окно товара, открытое сразу (после создания или по ссылке «Изменить»)."""
    context = get_context(request)
    overview = items_overview(context.unit_of_work(), session.user, category, context.clock())
    return render(
        "settings_items.html", session_user=session, header_run=run_status(context.unit_of_work(), session.user, context.clock()), overview=overview,
        flash_message=flash, form=dialog, results=link_results or [], toast=toast, undo_token=undo_token,
    )


def _form(request: Request, session: SessionUser, item_id: int | None, category: int | None) -> ItemForm:
    return item_form(get_context(request).unit_of_work(), session.user, item_id, category)


@dataclass(frozen=True, slots=True)
class ItemFields:
    name: str
    full_name: str | None
    quantity: int
    category_id: int
    group_id: int | None


def _item_fields(form: dict[str, str]) -> ItemFields:
    raw_group = form.get("group_id", "")
    return ItemFields(
        name=form.get("name", ""),
        full_name=form.get("full_name", "").strip() or None,
        quantity=_number(form, "quantity", 1, 999),
        category_id=_number(form, "category_id", 1, 2**62),
        group_id=None if raw_group == "" else _number(form, "group_id", 1, 2**62),
    )


def _number(form: dict[str, str], name: str, low: int, high: int) -> int:
    raw = form.get(name, "")
    if not raw.isdigit() or not low <= int(raw) <= high:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {name: "invalid"}})
    return int(raw)


def _filter(form: dict[str, str]) -> int | None:
    raw = form.get("filter", "")
    return int(raw) if raw.isdigit() else None
