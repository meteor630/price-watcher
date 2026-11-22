"""JSON-формы каталога. Веб и iPhone используют одни сценарии и версии записей."""

from dataclasses import asdict
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.api.v1_json import item_row_json, store_json
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.category import categories_overview, items_overview
from pricewatch.application.purchases import archive_item, purchase_form, purchase_item, return_to_list
from pricewatch.application.shopping import (
    add_links, create_category, create_group, create_item, delete_category, delete_group, delete_item, item_links_view,
    remove_link, rename_category, rename_group, reorder_groups, reorder_items, undo_delete, update_item,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import parse_rub
from pricewatch.domain.review import Decision
from pricewatch.application.reviews import decide, recheck

router = APIRouter(prefix="/api/v1/catalog")
EntityId = Annotated[int, Path(ge=1, le=2**62)]
Id = Annotated[int, Field(ge=1, le=2**62)]
Quantity = Annotated[int, Field(ge=1, le=999)]
Name = Annotated[str, Field(max_length=200)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Named(Body):
    name: Name


class Version(Body):
    version: Id


class Rename(Version, Named):
    """Версия редактируемой записи."""


class RemoveCategory(Version):
    move_to: Id | None = None


class RemoveGroup(Version):
    keep_items: bool


class Order(Version):
    ids: Annotated[list[Id], Field(min_length=1, max_length=500)]


class ItemOrder(Order):
    category_id: Id
    group_id: Id | None = None


class ItemFields(Named):
    full_name: Annotated[str, Field(max_length=500)] | None = None
    category_id: Id
    group_id: Id | None = None
    quantity: Quantity


class NewItem(ItemFields):
    links: Annotated[list[Annotated[str, Field(max_length=4096)]], Field(max_length=10)] = Field(default_factory=list)


class EditItem(ItemFields, Version):
    """Версия редактируемой записи."""


class LinkList(Body):
    links: Annotated[list[Annotated[str, Field(max_length=4096)]], Field(min_length=1, max_length=10)]


class PurchaseFields(Version):
    price: Annotated[str, Field(max_length=50)]
    quantity: Quantity
    store_id: Id | None = None
    day: Annotated[date, Field(strict=False)]


class Undo(Body):
    token: Annotated[str, Field(max_length=500)]


@router.get("/categories")
def category_overview(request: Request, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    return asdict(categories_overview(context.unit_of_work(), auth.user, context.clock()))


@router.get("/items")
def item_overview(request: Request, category_id: int | None = None, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    overview = items_overview(context.unit_of_work(), auth.user, category_id, context.clock())
    return {"items": [
        {"product": item_row_json(entry.row), "category_id": entry.category_id, "category_name": entry.category_name,
         "group_id": entry.row.group_id, "group_name": entry.group_name, "siblings": entry.siblings,
         "parent_version": entry.parent_version}
        for entry in overview.entries
    ]}


@router.post("/categories", status_code=201)
def new_category(request: Request, body: Named, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(),
                      lambda: {"id": create_category(context.unit_of_work(), auth.user, body.name).id}, 201)


@router.patch("/categories/{category_id}")
def edit_category(request: Request, category_id: EntityId, body: Rename, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "id": rename_category(context.unit_of_work(), auth.user, category_id, body.name, body.version).id,
    })


@router.post("/categories/{category_id}/remove")
def remove_category(request: Request, category_id: EntityId, body: RemoveCategory, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "undo_token": delete_category(context.unit_of_work(), context.secret_key, auth.user,
                                      category_id, body.version, body.move_to, context.clock()),
    })


@router.post("/categories/{category_id}/groups", status_code=201)
def new_group(request: Request, category_id: EntityId, body: Named, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(),
                      lambda: {"id": create_group(context.unit_of_work(), auth.user, category_id, body.name).id}, 201)


@router.patch("/groups/{group_id}")
def edit_group(request: Request, group_id: EntityId, body: Rename, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "id": rename_group(context.unit_of_work(), auth.user, group_id, body.name, body.version).id,
    })


@router.post("/groups/{group_id}/remove")
def remove_group(request: Request, group_id: EntityId, body: RemoveGroup, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "undo_token": delete_group(context.unit_of_work(), context.secret_key, auth.user,
                                   group_id, body.version, body.keep_items, context.clock()),
    })


@router.put("/categories/{category_id}/groups/order")
def move_groups(request: Request, category_id: EntityId, body: Order, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "version": reorder_groups(context.unit_of_work(), auth.user, category_id, body.ids, body.version),
    })


@router.post("/items", status_code=201)
def new_item(request: Request, body: NewItem, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def create() -> dict:
        item = create_item(context.unit_of_work(), auth.user, body.category_id, body.group_id, body.name, (body.full_name or "").strip() or None, body.quantity)
        results = add_links(context.unit_of_work, context.links, auth.user, item.id, body.links, context.clock())
        return {"id": item.id, "results": [asdict(result) for result in results]}

    return idempotent(request, auth.user.id, body.model_dump(), create, 201)


@router.patch("/items/{item_id}")
def edit_item(request: Request, item_id: EntityId, body: EditItem, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def save() -> dict:
        item = update_item(context.unit_of_work(), auth.user, item_id, body.version, name=body.name, full_name=(body.full_name or "").strip() or None,
                           quantity=body.quantity, category_id=body.category_id, group_id=body.group_id)
        return {"id": item.id}

    return idempotent(request, auth.user.id, body.model_dump(), save)


@router.post("/items/{item_id}/remove")
def remove_item(request: Request, item_id: EntityId, body: Version, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "undo_token": delete_item(context.unit_of_work(), context.secret_key, auth.user, item_id, body.version, context.clock()),
    })


@router.put("/items/order")
def move_items(request: Request, body: ItemOrder, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "version": reorder_items(context.unit_of_work(), auth.user, body.category_id, body.group_id, body.ids, body.version),
    })


@router.get("/items/{item_id}/links")
def links(request: Request, item_id: EntityId, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    return {"items": [asdict(link) for link in item_links_view(context.unit_of_work(), auth.user, item_id)]}


@router.post("/items/{item_id}/links")
def attach_links(request: Request, item_id: EntityId, body: LinkList, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, body.model_dump(), lambda: {
        "results": [asdict(result) for result in add_links(context.unit_of_work, context.links, auth.user, item_id, body.links, context.clock())],
    })


@router.delete("/items/{item_id}/links/{link_id}")
def detach_link(request: Request, item_id: EntityId, link_id: EntityId, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    return idempotent(request, auth.user.id, {}, lambda: {
        "undo_token": remove_link(context.unit_of_work(), context.secret_key, auth.user, item_id, link_id, context.clock()),
    })


@router.get("/items/{item_id}/purchase")
def purchase_defaults(request: Request, item_id: EntityId, auth: DeviceUser = Depends(app_user)) -> dict:
    context = get_context(request)
    form = purchase_form(context.unit_of_work(), auth.user, item_id, context.clock())
    return {**asdict(form), "stores": [store_json(store) for store in form.stores]}


@router.post("/items/{item_id}/purchase")
def buy_item(request: Request, item_id: EntityId, body: PurchaseFields, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)
    price = parse_rub(body.price, "purchase_price")
    return idempotent(request, auth.user.id, body.model_dump(mode="json"), lambda: {
        "undo_token": purchase_item(context.unit_of_work(), context.secret_key, auth.user, item_id, body.version,
                                    price_minor=price, quantity=body.quantity, store_id=body.store_id, day=body.day, now=context.clock()),
    })


@router.post("/items/{item_id}/{transition}")
def transition_item(request: Request, item_id: EntityId, transition: str, body: Version, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def apply() -> dict:
        if transition == "archive":
            token = archive_item(context.unit_of_work(), context.secret_key, auth.user, item_id, body.version, context.clock())
            return {"undo_token": token}
        if transition == "return":
            return {"id": return_to_list(context.unit_of_work(), auth.user, item_id, body.version).id}
        raise PriceWatcherError(ErrorCode.NOT_FOUND)

    return idempotent(request, auth.user.id, body.model_dump(), apply)


@router.post("/undo")
def undo(request: Request, body: Undo, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def restore() -> dict:
        _, identifier = undo_delete(context.unit_of_work(), context.secret_key, auth.user, body.token, context.clock())
        return {"id": identifier}

    return idempotent(request, auth.user.id, body.model_dump(), restore)

class ReviewAction(Body):
    action: Literal["accept", "reject", "recheck"]


@router.post("/reviews/{review_id}")
def review_action(request: Request, review_id: EntityId, body: ReviewAction, auth: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def apply() -> dict:
        now = context.clock()
        if body.action == "recheck":
            token = recheck(context.unit_of_work(), context.secret_key, auth.user, review_id, now).undo_token
        else:
            token = decide(context.unit_of_work(), context.secret_key, auth.user, review_id, Decision(body.action), now)
        return {"undo_token": token}

    return idempotent(request, auth.user.id, body.model_dump(), apply)
