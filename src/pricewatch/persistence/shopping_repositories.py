"""Репозитории списка покупок на PostgreSQL. Правила — в pricewatch.application.ports.

Операции, меняющие состав списка (создание, удаление, восстановление, перестановка,
смена группы), сначала поднимают версию родителя. Это блокирует строку родителя:
параллельные изменения одного списка идут по очереди, позиции не дублируются,
а клиент с устаревшим списком получает VERSION_CONFLICT.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, Row, Select, Table, Update, bindparam, exists, func, select, update
from sqlalchemy.exc import IntegrityError

from pricewatch.application.ports import UNCHANGED, ItemChanges, NewItem
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import (
    CATEGORY_NAME_MAX,
    GROUP_NAME_MAX,
    ITEM_FULL_NAME_MAX,
    ITEM_NAME_MAX,
    check_quantity,
    clean_name,
    name_key,
)
from pricewatch.domain.shopping import Category, Group, Item
from pricewatch.persistence.models import (
    CATEGORY_NAME_INDEX,
    GROUP_NAME_INDEX,
    CategoryRow,
    GroupRow,
    ItemRow,
    UserRow,
)

users: Table = UserRow.__table__
categories: Table = CategoryRow.__table__
groups: Table = GroupRow.__table__
items: Table = ItemRow.__table__


@dataclass(frozen=True, slots=True)
class OrderState:
    """Актуальный порядок для ответа VERSION_CONFLICT при перестановке."""

    ids: list[int]
    version: int


def _not_found() -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.NOT_FOUND)


def _conflict(current: object) -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.VERSION_CONFLICT, {"current": current})


def _wrong_ids() -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"ids": "must_list_all_records_once"}})


@contextmanager
def _unique_name(connection: Connection, index_name: str) -> Iterator[None]:
    """Нарушение уникальности имени — NAME_TAKEN; транзакция остаётся рабочей благодаря точке сохранения."""
    try:
        with connection.begin_nested():
            yield
    except IntegrityError as error:
        diagnostics = getattr(error.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == index_name:
            raise PriceWatcherError(ErrorCode.NAME_TAKEN) from error
        raise


def _check_order(ids: Sequence[int], active_ids: list[int]) -> None:
    if len(ids) != len(set(ids)) or set(ids) != set(active_ids):
        raise _wrong_ids()


def _set_positions(connection: Connection, table: Table, ids: Sequence[int]) -> None:
    connection.execute(
        update(table)
        .where(table.c.id == bindparam("row_id"))
        .values(position=bindparam("new_position"), updated_at=func.now()),
        [{"row_id": row_id, "new_position": position} for position, row_id in enumerate(ids)],
    )


def _to_category(row: Row) -> Category:
    return Category(id=row.id, user_id=row.user_id, name=row.name, position=row.position, version=row.version)


def _to_group(row: Row) -> Group:
    return Group(
        id=row.id,
        user_id=row.user_id,
        category_id=row.category_id,
        name=row.name,
        position=row.position,
        version=row.version,
    )


def _to_item(row: Row) -> Item:
    return Item(
        id=row.id,
        user_id=row.user_id,
        category_id=row.category_id,
        group_id=row.group_id,
        name=row.name,
        full_name=row.full_name,
        quantity=row.quantity,
        in_total=row.in_total,
        position=row.position,
        version=row.version,
    )


class _Parents:
    """Блокировка родителя через подъём его версии."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def bump_category_order(self, user_id: int, expected: int | None = None) -> int | None:
        query = update(users).where(users.c.id == user_id)
        if expected is not None:
            query = query.where(users.c.categories_order_version == expected)
        query = query.values(categories_order_version=users.c.categories_order_version + 1)
        return self.connection.execute(query.returning(users.c.categories_order_version)).scalar_one_or_none()

    def bump(self, table: Table, user_id: int, row_id: int, expected: int | None = None) -> int | None:
        query = update(table).where(table.c.id == row_id, table.c.user_id == user_id, table.c.deleted_at.is_(None))
        if expected is not None:
            query = query.where(table.c.version == expected)
        query = query.values(version=table.c.version + 1, updated_at=func.now())
        return self.connection.execute(query.returning(table.c.version)).scalar_one_or_none()

    def bump_item_parent(self, user_id: int, category_id: int, group_id: int | None) -> None:
        bumped = (
            self.bump(groups, user_id, group_id) if group_id is not None else self.bump(categories, user_id, category_id)
        )
        if bumped is None:
            raise _not_found()


class SqlCategoryRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.parents = _Parents(connection)

    def _active(self, user_id: int) -> Select:
        return select(categories).where(categories.c.user_id == user_id, categories.c.deleted_at.is_(None))

    def list_active(self, user_id: int) -> list[Category]:
        rows = self.connection.execute(self._active(user_id).order_by(categories.c.position, categories.c.id))
        return [_to_category(row) for row in rows]

    def get(self, user_id: int, category_id: int) -> Category:
        row = self.connection.execute(self._active(user_id).where(categories.c.id == category_id)).one_or_none()
        if row is None:
            raise _not_found()
        return _to_category(row)

    def create(self, user_id: int, name: str) -> Category:
        clean = clean_name(name, field="name", max_length=CATEGORY_NAME_MAX)
        if self.parents.bump_category_order(user_id) is None:
            raise _not_found()
        with _unique_name(self.connection, CATEGORY_NAME_INDEX):
            row = self.connection.execute(
                categories.insert()
                .values(user_id=user_id, name=clean, name_normalized=name_key(clean), position=self._next_position(user_id))
                .returning(categories)
            ).one()
        return _to_category(row)

    def rename(self, user_id: int, category_id: int, name: str, version: int) -> Category:
        clean = clean_name(name, field="name", max_length=CATEGORY_NAME_MAX)
        with _unique_name(self.connection, CATEGORY_NAME_INDEX):
            row = self.connection.execute(
                self._versioned(user_id, category_id, version)
                .values(name=clean, name_normalized=name_key(clean), version=categories.c.version + 1, updated_at=func.now())
                .returning(categories)
            ).one_or_none()
        if row is None:
            raise _conflict(self.get(user_id, category_id))
        return _to_category(row)

    def delete(self, user_id: int, category_id: int, version: int) -> None:
        self.get(user_id, category_id)
        self.parents.bump_category_order(user_id)
        deleted = self.connection.execute(
            self._versioned(user_id, category_id, version)
            .values(deleted_at=func.now(), version=categories.c.version + 1, updated_at=func.now())
            .returning(categories.c.id)
        ).one_or_none()
        if deleted is None:
            raise _conflict(self.get(user_id, category_id))

    def restore(self, user_id: int, category_id: int) -> Category:
        if self.parents.bump_category_order(user_id) is None:
            raise _not_found()
        with _unique_name(self.connection, CATEGORY_NAME_INDEX):
            row = self.connection.execute(
                update(categories)
                .where(
                    categories.c.id == category_id,
                    categories.c.user_id == user_id,
                    categories.c.deleted_at.is_not(None),
                )
                .values(
                    deleted_at=None,
                    position=self._next_position(user_id),
                    version=categories.c.version + 1,
                    updated_at=func.now(),
                )
                .returning(categories)
            ).one_or_none()
        if row is None:
            raise _not_found()
        return _to_category(row)

    def order_version(self, user_id: int) -> int:
        version = self.connection.execute(
            select(users.c.categories_order_version).where(users.c.id == user_id)
        ).scalar_one_or_none()
        if version is None:
            raise _not_found()
        return int(version)

    def reorder(self, user_id: int, ids: Sequence[int], order_version: int) -> int:
        new_version = self.parents.bump_category_order(user_id, expected=order_version)
        if new_version is None:
            current = OrderState([c.id for c in self.list_active(user_id)], self.order_version(user_id))
            raise _conflict(current)
        _check_order(ids, [c.id for c in self.list_active(user_id)])
        _set_positions(self.connection, categories, ids)
        return new_version

    def _versioned(self, user_id: int, category_id: int, version: int) -> Update:
        return update(categories).where(
            categories.c.id == category_id,
            categories.c.user_id == user_id,
            categories.c.deleted_at.is_(None),
            categories.c.version == version,
        )

    def _next_position(self, user_id: int) -> object:
        return (
            select(func.coalesce(func.max(categories.c.position) + 1, 0))
            .where(categories.c.user_id == user_id, categories.c.deleted_at.is_(None))
            .scalar_subquery()
        )


class SqlGroupRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.parents = _Parents(connection)

    def _active(self, user_id: int) -> Select:
        return select(groups).where(groups.c.user_id == user_id, groups.c.deleted_at.is_(None))

    def list_in_category(self, user_id: int, category_id: int) -> list[Group]:
        SqlCategoryRepository(self.connection).get(user_id, category_id)
        rows = self.connection.execute(
            self._active(user_id).where(groups.c.category_id == category_id).order_by(groups.c.position, groups.c.id)
        )
        return [_to_group(row) for row in rows]

    def get(self, user_id: int, group_id: int) -> Group:
        row = self.connection.execute(self._active(user_id).where(groups.c.id == group_id)).one_or_none()
        if row is None:
            raise _not_found()
        return _to_group(row)

    def create(self, user_id: int, category_id: int, name: str) -> Group:
        clean = clean_name(name, field="name", max_length=GROUP_NAME_MAX)
        if self.parents.bump(categories, user_id, category_id) is None:
            raise _not_found()
        with _unique_name(self.connection, GROUP_NAME_INDEX):
            row = self.connection.execute(
                groups.insert()
                .values(
                    user_id=user_id,
                    category_id=category_id,
                    name=clean,
                    name_normalized=name_key(clean),
                    position=self._next_position(category_id),
                )
                .returning(groups)
            ).one()
        return _to_group(row)

    def rename(self, user_id: int, group_id: int, name: str, version: int) -> Group:
        clean = clean_name(name, field="name", max_length=GROUP_NAME_MAX)
        with _unique_name(self.connection, GROUP_NAME_INDEX):
            row = self.connection.execute(
                self._versioned(user_id, group_id, version)
                .values(name=clean, name_normalized=name_key(clean), version=groups.c.version + 1, updated_at=func.now())
                .returning(groups)
            ).one_or_none()
        if row is None:
            raise _conflict(self.get(user_id, group_id))
        return _to_group(row)

    def delete(self, user_id: int, group_id: int, version: int) -> None:
        group = self.get(user_id, group_id)
        self.parents.bump(categories, user_id, group.category_id)
        deleted = self.connection.execute(
            self._versioned(user_id, group_id, version)
            .values(deleted_at=func.now(), version=groups.c.version + 1, updated_at=func.now())
            .returning(groups.c.id)
        ).one_or_none()
        if deleted is None:
            raise _conflict(self.get(user_id, group_id))

    def restore(self, user_id: int, group_id: int) -> Group:
        category_id = self.connection.execute(
            select(groups.c.category_id).where(
                groups.c.id == group_id, groups.c.user_id == user_id, groups.c.deleted_at.is_not(None)
            )
        ).scalar_one_or_none()
        if category_id is None or self.parents.bump(categories, user_id, category_id) is None:
            raise _not_found()
        with _unique_name(self.connection, GROUP_NAME_INDEX):
            row = self.connection.execute(
                update(groups)
                .where(groups.c.id == group_id, groups.c.user_id == user_id, groups.c.deleted_at.is_not(None))
                .values(
                    deleted_at=None,
                    position=self._next_position(category_id),
                    version=groups.c.version + 1,
                    updated_at=func.now(),
                )
                .returning(groups)
            ).one_or_none()
        if row is None:
            raise _not_found()
        return _to_group(row)

    def reorder(self, user_id: int, category_id: int, ids: Sequence[int], category_version: int) -> int:
        new_version = self.parents.bump(categories, user_id, category_id, expected=category_version)
        if new_version is None:
            raise _conflict(SqlCategoryRepository(self.connection).get(user_id, category_id))
        _check_order(ids, [g.id for g in self.list_in_category(user_id, category_id)])
        _set_positions(self.connection, groups, ids)
        return new_version

    def _versioned(self, user_id: int, group_id: int, version: int) -> Update:
        return update(groups).where(
            groups.c.id == group_id,
            groups.c.user_id == user_id,
            groups.c.deleted_at.is_(None),
            groups.c.version == version,
        )

    def _next_position(self, category_id: int) -> object:
        return (
            select(func.coalesce(func.max(groups.c.position) + 1, 0))
            .where(groups.c.category_id == category_id, groups.c.deleted_at.is_(None))
            .scalar_subquery()
        )


class SqlItemRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.parents = _Parents(connection)

    def _active(self, user_id: int) -> Select:
        return select(items).where(items.c.user_id == user_id, items.c.deleted_at.is_(None))

    def list_in_category(self, user_id: int, category_id: int) -> list[Item]:
        SqlCategoryRepository(self.connection).get(user_id, category_id)
        rows = self.connection.execute(
            self._active(user_id)
            .where(items.c.category_id == category_id)
            .order_by(items.c.group_id.nulls_first(), items.c.position, items.c.id)
        )
        return [_to_item(row) for row in rows]

    def get(self, user_id: int, item_id: int) -> Item:
        row = self.connection.execute(self._active(user_id).where(items.c.id == item_id)).one_or_none()
        if row is None:
            raise _not_found()
        return _to_item(row)

    def create(self, user_id: int, new: NewItem) -> Item:
        name = clean_name(new.name, field="name", max_length=ITEM_NAME_MAX)
        full_name = self._full_name(new.full_name)
        quantity = check_quantity(new.quantity)
        SqlCategoryRepository(self.connection).get(user_id, new.category_id)
        if new.group_id is not None:
            self._check_group(user_id, new.category_id, new.group_id)
        self.parents.bump_item_parent(user_id, new.category_id, new.group_id)
        row = self.connection.execute(
            items.insert()
            .values(
                user_id=user_id,
                category_id=new.category_id,
                group_id=new.group_id,
                name=name,
                full_name=full_name,
                quantity=quantity,
                in_total=bool(new.in_total),
                position=self._next_position(new.category_id, new.group_id),
            )
            .returning(items)
        ).one()
        return _to_item(row)

    def update(self, user_id: int, item_id: int, version: int, changes: ItemChanges) -> Item:
        current = self.get(user_id, item_id)
        if current.version != version:
            raise _conflict(current)
        values: dict[str, object] = {}
        if changes.name is not UNCHANGED:
            values["name"] = clean_name(changes.name, field="name", max_length=ITEM_NAME_MAX)
        if changes.full_name is not UNCHANGED:
            values["full_name"] = self._full_name(changes.full_name)
        if changes.quantity is not UNCHANGED:
            values["quantity"] = check_quantity(changes.quantity)
        if changes.in_total is not UNCHANGED:
            values["in_total"] = bool(changes.in_total)
        if changes.group_id is not UNCHANGED and changes.group_id != current.group_id:
            if changes.group_id is not None:
                self._check_group(user_id, current.category_id, changes.group_id)
            self._bump_both_parents(user_id, current, changes.group_id)
            values["group_id"] = changes.group_id
            values["position"] = self._next_position(current.category_id, changes.group_id)
        row = self.connection.execute(
            update(items)
            .where(
                items.c.id == item_id,
                items.c.user_id == user_id,
                items.c.deleted_at.is_(None),
                items.c.version == version,
            )
            .values(**values, version=items.c.version + 1, updated_at=func.now())
            .returning(items)
        ).one_or_none()
        if row is None:
            raise _conflict(self.get(user_id, item_id))
        return _to_item(row)

    def delete(self, user_id: int, item_id: int, version: int) -> None:
        current = self.get(user_id, item_id)
        self.parents.bump_item_parent(user_id, current.category_id, current.group_id)
        deleted = self.connection.execute(
            update(items)
            .where(
                items.c.id == item_id,
                items.c.user_id == user_id,
                items.c.deleted_at.is_(None),
                items.c.version == version,
            )
            .values(deleted_at=func.now(), version=items.c.version + 1, updated_at=func.now())
            .returning(items.c.id)
        ).one_or_none()
        if deleted is None:
            raise _conflict(self.get(user_id, item_id))

    def restore(self, user_id: int, item_id: int) -> Item:
        row = self.connection.execute(
            select(items.c.category_id, items.c.group_id).where(
                items.c.id == item_id, items.c.user_id == user_id, items.c.deleted_at.is_not(None)
            )
        ).one_or_none()
        if row is None:
            raise _not_found()
        SqlCategoryRepository(self.connection).get(user_id, row.category_id)
        self.parents.bump_item_parent(user_id, row.category_id, row.group_id)
        restored = self.connection.execute(
            update(items)
            .where(items.c.id == item_id, items.c.user_id == user_id, items.c.deleted_at.is_not(None))
            .values(
                deleted_at=None,
                position=self._next_position(row.category_id, row.group_id),
                version=items.c.version + 1,
                updated_at=func.now(),
            )
            .returning(items)
        ).one()
        return _to_item(restored)

    def reorder(
        self, user_id: int, category_id: int, group_id: int | None, ids: Sequence[int], parent_version: int
    ) -> int:
        if group_id is None:
            new_version = self.parents.bump(categories, user_id, category_id, expected=parent_version)
            if new_version is None:
                raise _conflict(SqlCategoryRepository(self.connection).get(user_id, category_id))
        else:
            self._check_group(user_id, category_id, group_id)
            new_version = self.parents.bump(groups, user_id, group_id, expected=parent_version)
            if new_version is None:
                raise _conflict(SqlGroupRepository(self.connection).get(user_id, group_id))
        members = [item.id for item in self.list_in_category(user_id, category_id) if item.group_id == group_id]
        _check_order(ids, members)
        _set_positions(self.connection, items, ids)
        return new_version

    def _check_group(self, user_id: int, category_id: int, group_id: int) -> None:
        group = SqlGroupRepository(self.connection).get(user_id, group_id)
        if group.category_id != category_id:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"group_id": "other_category"}})

    def _bump_both_parents(self, user_id: int, current: Item, new_group_id: int | None) -> None:
        # Старый и новый родитель блокируются в одном порядке, чтобы два встречных переноса не взаимоблокировались.
        parents = sorted({current.group_id, new_group_id}, key=lambda g: -1 if g is None else g)
        for group_id in parents:
            self.parents.bump_item_parent(user_id, current.category_id, group_id)

    @staticmethod
    def _full_name(value: str | None) -> str | None:
        return None if value is None else clean_name(value, field="full_name", max_length=ITEM_FULL_NAME_MAX)

    def _next_position(self, category_id: int, group_id: int | None) -> object:
        return (
            select(func.coalesce(func.max(items.c.position) + 1, 0))
            .where(
                items.c.category_id == category_id,
                items.c.group_id.is_not_distinct_from(group_id),
                items.c.deleted_at.is_(None),
            )
            .scalar_subquery()
        )


class SqlPersonalDataRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def blocked_owners(self, blocked_before: datetime) -> list[int]:
        has_data = (
            exists().where(categories.c.user_id == users.c.id, categories.c.deleted_at.is_(None))
            | exists().where(groups.c.user_id == users.c.id, groups.c.deleted_at.is_(None))
            | exists().where(items.c.user_id == users.c.id, items.c.deleted_at.is_(None))
        )
        return list(
            self.connection.execute(
                select(users.c.id)
                .where(users.c.disabled_at.is_not(None), users.c.disabled_at <= blocked_before, has_data)
                .order_by(users.c.id)
                # Пока администратор меняет пользователя (например, разблокирует), его данные не трогаются.
                .with_for_update(skip_locked=True, of=users)
            ).scalars()
        )

    def soft_delete_all(self, user_id: int, now: datetime) -> None:
        for table in (items, groups, categories):
            self.connection.execute(
                update(table)
                .where(table.c.user_id == user_id, table.c.deleted_at.is_(None))
                .values(deleted_at=now, version=table.c.version + 1, updated_at=now)
            )
        self.connection.execute(
            update(users).where(users.c.id == user_id).values(categories_order_version=users.c.categories_order_version + 1)
        )
