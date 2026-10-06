"""Редактирование списка: категории и группы (SITE_DESIGN_SPEC.md, раздел 8; DEVELOPMENT_ARCHITECTURE.md, раздел 5).

- Имена проверяются и сравниваются без учёта регистра, «ё/е» и пробелов (domain.names); повтор — NAME_TAKEN.
- Изменения — по версии записи (VERSION_CONFLICT с актуальным состоянием); перестановка — полным списком
  и версией родителя: порядок категорий — версия порядка пользователя, групп — версия категории.
- Удаление мягкое, ответ несёт токен «Отменить» (15 секунд). У непустой категории выбор: перенести товары
  в другую категорию (без группы, в конец) или удалить вместе с ней. У группы — оставить товары в категории
  «без группы» или удалить вместе с группой. Отмена возвращает удалённую запись; перенесённые товары остаются там,
  куда их перенесли.
"""

from collections.abc import Sequence
from datetime import datetime

from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.shopping import Category, Group
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


def undo_delete(uow: UnitOfWork, key: bytes, user: User, token: str, now: datetime) -> tuple[UndoKind, int]:
    """Вернуть удалённое по токену; просроченный, чужой и подделанный токены — NOT_FOUND."""
    kind, entity_id = read_undo(key, user.id, token, now)
    with uow:
        if kind is UndoKind.CATEGORY:
            uow.categories.restore(user.id, entity_id)
        else:
            uow.groups.restore(user.id, entity_id)
        uow.commit()
    return kind, entity_id
