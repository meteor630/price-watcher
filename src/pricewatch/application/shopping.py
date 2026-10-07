"""Редактирование списка: категории, группы, товары и их ссылки (SITE_DESIGN_SPEC.md, раздел 8; DEVELOPMENT_ARCHITECTURE.md, раздел 5).

- Имена проверяются и сравниваются без учёта регистра, «ё/е» и пробелов (domain.names); повтор — NAME_TAKEN.
- Изменения — по версии записи (VERSION_CONFLICT с актуальным состоянием); перестановка — полным списком
  и версией родителя: порядок категорий — версия порядка пользователя, групп — версия категории.
- Удаление мягкое, ответ несёт токен «Отменить» (15 секунд). У непустой категории выбор: перенести товары
  в другую категорию (без группы, в конец) или удалить вместе с ней. У группы — оставить товары в категории
  «без группы» или удалить вместе с группой. Отмена возвращает удалённую запись; перенесённые товары остаются там,
  куда их перенесли.
- Товар: создать, изменить (имя, полное имя, количество, категория и группа), удалить с отменой.
- Ссылки добавляются по одной (application.links.add_link): ошибка одной не мешает остальным.
  Снятая ссылка закрывает привязку, история цены остаётся; отмена возвращает привязку, если у товара
  тем временем не появилась другая ссылка того же магазина.
- Состояние ссылки: не проверена, проверяется, цена найдена, ожидает плановой проверки, не удалось найти цену,
  сайт отключён администратором; у найденной цены — источник («данные сайта» или «найдено на странице»).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pricewatch.application.links import add_link
from pricewatch.application.ports import UNCHANGED, ItemChanges, LinkResolver, LinkState, NewItem, UnitOfWork
from pricewatch.application.reviews import undo_decision, undo_recheck
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.money import format_rub
from pricewatch.domain.shopping import REGION_CODE, Category, Group, Item, ItemState
from pricewatch.domain.undo import UndoKind, issue_undo, read_undo


def create_category(uow: UnitOfWork, user: User, name: str) -> Category:
    with uow:
        category = uow.categories.create(user.id, name)
        uow.commit()
    return category


def rename_category(uow: UnitOfWork, user: User, category_id: int, name: str, version: int) -> Category:
    with uow:
        category = uow.categories.rename(user.id, category_id, name, version)
        uow.commit()
    return category


def delete_category(uow: UnitOfWork, key: bytes, user: User, category_id: int, version: int, move_to: int | None, now: datetime) -> str:
    """Токен отмены. `move_to` — категория, куда перенести товары; `None` — удалить их вместе с категорией."""
    if move_to == category_id:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"move_to": "same_category"}})
    with uow:
        if move_to is not None:
            uow.items.move_category_items(user.id, category_id, move_to)
        uow.categories.delete(user.id, category_id, version)
        uow.commit()
    return issue_undo(key, user.id, UndoKind.CATEGORY, category_id, now)


def reorder_categories(uow: UnitOfWork, user: User, ids: Sequence[int], order_version: int) -> int:
    with uow:
        version = uow.categories.reorder(user.id, ids, order_version)
        uow.commit()
    return version


def create_group(uow: UnitOfWork, user: User, category_id: int, name: str) -> Group:
    with uow:
        group = uow.groups.create(user.id, category_id, name)
        uow.commit()
    return group


def rename_group(uow: UnitOfWork, user: User, group_id: int, name: str, version: int) -> Group:
    with uow:
        group = uow.groups.rename(user.id, group_id, name, version)
        uow.commit()
    return group


def delete_group(uow: UnitOfWork, key: bytes, user: User, group_id: int, version: int, keep_items: bool, now: datetime) -> str:
    """Токен отмены. `keep_items` — товары остаются в категории без группы; иначе удаляются вместе с группой."""
    with uow:
        if keep_items:
            uow.items.ungroup_items(user.id, group_id)
        uow.groups.delete(user.id, group_id, version)
        uow.commit()
    return issue_undo(key, user.id, UndoKind.GROUP, group_id, now)


def reorder_groups(uow: UnitOfWork, user: User, category_id: int, ids: Sequence[int], category_version: int) -> int:
    with uow:
        version = uow.groups.reorder(user.id, category_id, ids, category_version)
        uow.commit()
    return version


UNDONE_STATES = {UndoKind.PURCHASE: ItemState.PURCHASED, UndoKind.ARCHIVE: ItemState.ARCHIVED}


def undo_delete(uow: UnitOfWork, key: bytes, user: User, token: str, now: datetime) -> tuple[UndoKind, int]:
    """Вернуть удалённое (или купленный и архивный товар — в список) по токену; просроченный, чужой и подделанный токены — NOT_FOUND."""
    kind, entity_id = read_undo(key, user.id, token, now)
    with uow:
        if kind is UndoKind.CATEGORY:
            uow.categories.restore(user.id, entity_id)
        elif kind is UndoKind.GROUP:
            uow.groups.restore(user.id, entity_id)
        elif kind is UndoKind.ITEM:
            uow.items.restore(user.id, entity_id)
        elif kind is UndoKind.LINK:
            if not uow.item_links.reopen(user.id, entity_id):
                raise PriceWatcherError(ErrorCode.NOT_FOUND, {"undo": "replaced"})
        elif kind is UndoKind.DECISION:
            undo_decision(uow, user, entity_id, now)
        elif kind is UndoKind.RECHECK:
            undo_recheck(uow, user, entity_id, now)
        else:
            item = uow.items.get(user.id, entity_id)
            # Товар уже вернули в список или перевели дальше — отменять нечего.
            if item.state is not UNDONE_STATES[kind]:
                raise PriceWatcherError(ErrorCode.NOT_FOUND, {"undo": "changed"})
            uow.items.return_to_list(user.id, entity_id, item.version)
        uow.commit()
    return kind, entity_id


# --- товары ---

NOT_FOUND_CODES = frozenset({"PRICE_NOT_FOUND", "LAYOUT_CHANGED", "UNSUPPORTED_CURRENCY"})
SOURCE_LABELS = {"site_data": "данные сайта", "page": "найдено на странице"}


class LinkStatus(StrEnum):
    UNCHECKED = "unchecked"
    CHECKING = "checking"
    FOUND = "found"
    WAITING = "waiting"
    NOT_FOUND = "not_found"
    STORE_DISABLED = "store_disabled"


@dataclass(frozen=True, slots=True)
class LinkView:
    item_link_id: int
    url: str
    store_name: str
    color_token: str
    status: LinkStatus
    price_text: str | None
    source_label: str | None
    checked_at: datetime | None


@dataclass(frozen=True, slots=True)
class LinkAdded:
    url: str
    # Понятная причина, если ссылку добавить не удалось; None — добавлена.
    error: str | None


def create_item(uow: UnitOfWork, user: User, category_id: int, group_id: int | None, name: str, full_name: str | None, quantity: int) -> Item:
    with uow:
        item = uow.items.create(user.id, NewItem(category_id=category_id, name=name, group_id=group_id, full_name=full_name, quantity=quantity))
        uow.commit()
    return item


def update_item(
    uow: UnitOfWork, user: User, item_id: int, version: int, *, name: str, full_name: str | None, quantity: int, category_id: int,
    group_id: int | None,
) -> Item:
    with uow:
        current = uow.items.get(user.id, item_id)
        changes = ItemChanges(
            name=name, full_name=full_name, quantity=quantity,
            category_id=category_id if category_id != current.category_id else UNCHANGED,
            group_id=group_id,
        )
        item = uow.items.update(user.id, item_id, version, changes)
        uow.commit()
    return item


def delete_item(uow: UnitOfWork, key: bytes, user: User, item_id: int, version: int, now: datetime) -> str:
    with uow:
        uow.items.delete(user.id, item_id, version)
        uow.commit()
    return issue_undo(key, user.id, UndoKind.ITEM, item_id, now)


def reorder_items(uow: UnitOfWork, user: User, category_id: int, group_id: int | None, ids: Sequence[int], parent_version: int) -> int:
    with uow:
        version = uow.items.reorder(user.id, category_id, group_id, ids, parent_version)
        uow.commit()
    return version


def reorder_group_items(uow: UnitOfWork, user: User, group_id: int, ids: Sequence[int], group_version: int) -> int:
    """Порядок товаров группы для приложения: версия родителя — версия группы."""
    with uow:
        group = uow.groups.get(user.id, group_id)
        version = uow.items.reorder(user.id, group.category_id, group_id, ids, group_version)
        uow.commit()
    return version


def add_links(
    uow_factory: Callable[[], UnitOfWork], resolver: LinkResolver, user: User, item_id: int, urls: Sequence[str], now: datetime
) -> list[LinkAdded]:
    """Каждая ссылка — отдельно: ошибка одной (адрес, отключённый сайт, лимит) не отменяет остальные."""
    results = []
    for url in (url.strip() for url in urls):
        if not url:
            continue
        try:
            add_link(uow_factory(), resolver, user.id, item_id, url, REGION_CODE, now)
        except PriceWatcherError as error:
            if error.code is ErrorCode.NOT_FOUND:
                raise  # товара нет — дальше добавлять некуда
            results.append(LinkAdded(url, SAFE_MESSAGES[error.code]))
        else:
            results.append(LinkAdded(url, None))
    return results


def remove_link(uow: UnitOfWork, key: bytes, user: User, item_id: int, item_link_id: int, now: datetime) -> str:
    with uow:
        uow.item_links.lock_item(user.id, item_id)
        if uow.item_links.active_binding(user.id, item_id, item_link_id) is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        uow.item_links.close(item_link_id, now)
        uow.commit()
    return issue_undo(key, user.id, UndoKind.LINK, item_link_id, now)


def item_links_view(uow: UnitOfWork, user: User, item_id: int) -> list[LinkView]:
    with uow:
        uow.items.get(user.id, item_id)
        states = uow.price_data.link_states(user.id, item_id)
    return [_link_view(state) for state in states]


def _link_view(state: LinkState) -> LinkView:
    price = None
    source = None
    if not state.store_enabled:
        status = LinkStatus.STORE_DISABLED
    elif state.checking:
        status = LinkStatus.CHECKING
    elif state.last_status is None:
        status = LinkStatus.UNCHECKED
    elif state.last_status == "ok":
        status = LinkStatus.FOUND
        shown = state.last_card_price_minor or state.last_price_minor
        price = "нет в наличии" if state.last_availability == "out_of_stock" or shown is None else format_rub(shown)
        source = SOURCE_LABELS.get(state.last_source or "")
    elif state.last_status in NOT_FOUND_CODES:
        status = LinkStatus.NOT_FOUND
    else:
        status = LinkStatus.WAITING
    return LinkView(state.item_link_id, state.url, state.store_name, state.color_token, status, price, source, state.last_checked_at)



@dataclass(frozen=True, slots=True)
class ItemForm:
    """Данные окна товара: товар (None — новый), категории, группы выбранной категории, ссылки с состояниями."""

    item: Item | None
    categories: list[Category]
    category_id: int
    groups: list[Group]
    links: list[LinkView]


def item_form(uow: UnitOfWork, user: User, item_id: int | None, category_id: int | None) -> ItemForm:
    with uow:
        categories = uow.categories.list_active(user.id)
        if not categories:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"category_id": "no_categories"}})
        item = uow.items.get(user.id, item_id) if item_id is not None else None
        chosen = item.category_id if item is not None and category_id is None else category_id or categories[0].id
        if all(category.id != chosen for category in categories):
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        groups = uow.groups.list_in_category(user.id, chosen)
        links = [_link_view(state) for state in uow.price_data.link_states(user.id, item_id)] if item_id is not None else []
    return ItemForm(item, categories, chosen, groups, links)
