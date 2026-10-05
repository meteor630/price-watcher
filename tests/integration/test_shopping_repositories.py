"""Репозитории списка покупок: владелец, версии, мягкое удаление, перестановка, уникальность имён."""

import inspect
import uuid
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine, text

from pricewatch.application import ports
from pricewatch.application.ports import ItemChanges, NewItem
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.shopping_repositories import (
    OrderState,
    SqlCategoryRepository,
    SqlGroupRepository,
    SqlItemRepository,
)
from pricewatch.persistence.unit_of_work import SqlUnitOfWork


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def uow(engine: Engine) -> Iterator[SqlUnitOfWork]:
    """Единица работы без commit: всё, что сделал тест, откатывается."""
    with SqlUnitOfWork(engine) as work:
        yield work


def add_user(connection, login: str | None = None) -> int:  # type: ignore[no-untyped-def]
    return connection.execute(
        text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"),
        {"l": login or f"u_{uuid.uuid4().hex[:12]}"},
    ).scalar_one()


@pytest.fixture
def alice(uow: SqlUnitOfWork) -> int:
    return add_user(uow.categories.connection)


@pytest.fixture
def bob(uow: SqlUnitOfWork) -> int:
    return add_user(uow.categories.connection)


def error_of(action: Callable[[], object]) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value


def code_of(action: Callable[[], object]) -> ErrorCode:
    return error_of(action).code


# --- категории ---------------------------------------------------------------


def test_category_create_cleans_name_and_appends(uow: SqlUnitOfWork, alice: int) -> None:
    first = uow.categories.create(alice, "  Компьютер ")
    second = uow.categories.create(alice, "Рабочее   место")

    assert (first.name, first.position, first.version) == ("Компьютер", 0, 1)
    assert (second.name, second.position) == ("Рабочее место", 1)
    assert [c.id for c in uow.categories.list_active(alice)] == [first.id, second.id]


@pytest.mark.parametrize("duplicate", ["КОМПЬЮТЕР", "  компьютер", "Компьютер"])
def test_category_name_taken_ignoring_case_space(uow: SqlUnitOfWork, alice: int, duplicate: str) -> None:
    uow.categories.create(alice, "Компьютер")

    assert code_of(lambda: uow.categories.create(alice, duplicate)) is ErrorCode.NAME_TAKEN
    # транзакция осталась рабочей благодаря точке сохранения
    assert len(uow.categories.list_active(alice)) == 1


def test_category_yo_and_e_are_one_name(uow: SqlUnitOfWork, alice: int) -> None:
    uow.categories.create(alice, "Ёлочные игрушки")

    assert code_of(lambda: uow.categories.create(alice, "елочные игрушки")) is ErrorCode.NAME_TAKEN


def test_same_category_name_for_other_user(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    uow.categories.create(alice, "Компьютер")

    assert uow.categories.create(bob, "Компьютер").user_id == bob


def test_category_create_for_unknown_user(uow: SqlUnitOfWork) -> None:
    assert code_of(lambda: uow.categories.create(10**12, "Новая")) is ErrorCode.NOT_FOUND


def test_category_create_validates_name(uow: SqlUnitOfWork, alice: int) -> None:
    error = error_of(lambda: uow.categories.create(alice, "   "))

    assert error.code is ErrorCode.VALIDATION_ERROR
    assert error.details == {"fields": {"name": "empty"}}


def test_category_get_only_own_and_active(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    category = uow.categories.create(alice, "А")

    assert uow.categories.get(alice, category.id) == category
    assert code_of(lambda: uow.categories.get(bob, category.id)) is ErrorCode.NOT_FOUND
    assert code_of(lambda: uow.categories.get(alice, 10**12)) is ErrorCode.NOT_FOUND
    uow.categories.delete(alice, category.id, category.version)
    assert code_of(lambda: uow.categories.get(alice, category.id)) is ErrorCode.NOT_FOUND


def test_category_list_only_own_active(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    keep = uow.categories.create(alice, "А")
    gone = uow.categories.create(alice, "Б")
    uow.categories.create(bob, "Чужая")
    uow.categories.delete(alice, gone.id, gone.version)

    assert uow.categories.list_active(alice) == [keep]


def test_category_rename(uow: SqlUnitOfWork, alice: int) -> None:
    category = uow.categories.create(alice, "А")

    renamed = uow.categories.rename(alice, category.id, " Б ", category.version)

    assert (renamed.name, renamed.version) == ("Б", 2)


def test_category_rename_own_name_in_other_case(uow: SqlUnitOfWork, alice: int) -> None:
    category = uow.categories.create(alice, "компьютер")

    assert uow.categories.rename(alice, category.id, "Компьютер", 1).name == "Компьютер"


def test_category_rename_stale_version_returns_current(uow: SqlUnitOfWork, alice: int) -> None:
    category = uow.categories.create(alice, "А")
    current = uow.categories.rename(alice, category.id, "Б", 1)

    error = error_of(lambda: uow.categories.rename(alice, category.id, "В", 1))

    assert error.code is ErrorCode.VERSION_CONFLICT
    assert error.details["current"] == current


def test_category_rename_errors(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    first = uow.categories.create(alice, "А")
    uow.categories.create(alice, "Б")

    assert code_of(lambda: uow.categories.rename(alice, first.id, "б", 1)) is ErrorCode.NAME_TAKEN
    assert code_of(lambda: uow.categories.rename(bob, first.id, "В", 1)) is ErrorCode.NOT_FOUND
    uow.categories.delete(alice, first.id, 1)
    assert code_of(lambda: uow.categories.rename(alice, first.id, "В", 2)) is ErrorCode.NOT_FOUND


def test_category_delete_and_restore_to_end(uow: SqlUnitOfWork, alice: int) -> None:
    first = uow.categories.create(alice, "А")
    second = uow.categories.create(alice, "Б")
    order_before = uow.categories.order_version(alice)

    uow.categories.delete(alice, first.id, first.version)
    assert uow.categories.order_version(alice) == order_before + 1
    restored = uow.categories.restore(alice, first.id)

    assert restored.position > second.position
    assert [c.id for c in uow.categories.list_active(alice)] == [second.id, first.id]


def test_category_delete_errors(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    category = uow.categories.create(alice, "А")

    assert code_of(lambda: uow.categories.delete(bob, category.id, 1)) is ErrorCode.NOT_FOUND
    error = error_of(lambda: uow.categories.delete(alice, category.id, 7))
    assert error.code is ErrorCode.VERSION_CONFLICT
    assert error.details["current"] == category


def test_category_restore_errors(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    category = uow.categories.create(alice, "А")

    assert code_of(lambda: uow.categories.restore(alice, category.id)) is ErrorCode.NOT_FOUND  # не удалена
    uow.categories.delete(alice, category.id, 1)
    assert code_of(lambda: uow.categories.restore(bob, category.id)) is ErrorCode.NOT_FOUND
    uow.categories.create(alice, "а")
    assert code_of(lambda: uow.categories.restore(alice, category.id)) is ErrorCode.NAME_TAKEN


def test_category_reorder(uow: SqlUnitOfWork, alice: int) -> None:
    a, b, c = (uow.categories.create(alice, name) for name in "АБВ")
    version = uow.categories.order_version(alice)

    new_version = uow.categories.reorder(alice, [c.id, a.id, b.id], version)

    assert new_version == version + 1
    assert [x.id for x in uow.categories.list_active(alice)] == [c.id, a.id, b.id]


def test_category_reorder_stale_version_returns_current_order(uow: SqlUnitOfWork, alice: int) -> None:
    a, b = uow.categories.create(alice, "А"), uow.categories.create(alice, "Б")
    version = uow.categories.order_version(alice)

    error = error_of(lambda: uow.categories.reorder(alice, [b.id, a.id], version - 1))

    assert error.code is ErrorCode.VERSION_CONFLICT
    assert error.details["current"] == OrderState([a.id, b.id], version)


@pytest.mark.parametrize("variant", ["missing", "extra", "duplicate", "foreign"])
def test_category_reorder_requires_exact_list(uow: SqlUnitOfWork, alice: int, bob: int, variant: str) -> None:
    a, b = uow.categories.create(alice, "А"), uow.categories.create(alice, "Б")
    foreign = uow.categories.create(bob, "Чужая")
    ids = {
        "missing": [a.id],
        "extra": [a.id, b.id, 10**12],
        "duplicate": [a.id, a.id, b.id],
        "foreign": [a.id, b.id, foreign.id],
    }[variant]

    error = error_of(lambda: uow.categories.reorder(alice, ids, uow.categories.order_version(alice)))

    assert error.code is ErrorCode.VALIDATION_ERROR
    assert error.details == {"fields": {"ids": "must_list_all_records_once"}}


# --- группы ------------------------------------------------------------------


def test_group_create_list_and_category_version(uow: SqlUnitOfWork, alice: int) -> None:
    category = uow.categories.create(alice, "А")

    first = uow.groups.create(alice, category.id, "Мониторы")
    second = uow.groups.create(alice, category.id, "Клавиатуры")

    assert [g.id for g in uow.groups.list_in_category(alice, category.id)] == [first.id, second.id]
    assert uow.categories.get(alice, category.id).version == category.version + 2


def test_group_name_unique_within_category_only(uow: SqlUnitOfWork, alice: int) -> None:
    first, second = uow.categories.create(alice, "А"), uow.categories.create(alice, "Б")
    uow.groups.create(alice, first.id, "Мониторы")

    assert code_of(lambda: uow.groups.create(alice, first.id, "МОНИТОРЫ")) is ErrorCode.NAME_TAKEN
    assert uow.groups.create(alice, second.id, "Мониторы").category_id == second.id


def test_group_in_foreign_or_deleted_category(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    foreign = uow.categories.create(bob, "Чужая")
    gone = uow.categories.create(alice, "Удалённая")
    uow.categories.delete(alice, gone.id, 1)

    assert code_of(lambda: uow.groups.create(alice, foreign.id, "Г")) is ErrorCode.NOT_FOUND
    assert code_of(lambda: uow.groups.create(alice, gone.id, "Г")) is ErrorCode.NOT_FOUND
    assert code_of(lambda: uow.groups.list_in_category(alice, foreign.id)) is ErrorCode.NOT_FOUND


def test_group_get_rename_delete_restore(uow: SqlUnitOfWork, alice: int, bob: int) -> None:
    category = uow.categories.create(alice, "А")
    group = uow.groups.create(alice, category.id, "Г")

    assert code_of(lambda: uow.groups.get(bob, group.id)) is ErrorCode.NOT_FOUND
    renamed = uow.groups.rename(alice, group.id, "Г2", group.version)
    assert code_of(lambda: uow.groups.rename(alice, group.id, "Г3", group.version)) is ErrorCode.VERSION_CONFLICT
    assert code_of(lambda: uow.groups.delete(bob, group.id, renamed.version)) is ErrorCode.NOT_FOUND
    uow.groups.delete(alice, group.id, renamed.version)
    assert uow.groups.list_in_category(alice, category.id) == []
    assert uow.groups.restore(alice, group.id).name == "Г2"


def test_group_restore_when_name_taken_or_category_deleted(uow: SqlUnitOfWork, alice: int) -> None:
    category = uow.categories.create(alice, "А")
    group = uow.groups.create(alice, category.id, "Г")
    uow.groups.delete(alice, group.id, 1)
    uow.groups.create(alice, category.id, "г")

    assert code_of(lambda: uow.groups.restore(alice, group.id)) is ErrorCode.NAME_TAKEN
    uow.categories.delete(alice, category.id, uow.categories.get(alice, category.id).version)
    assert code_of(lambda: uow.groups.restore(alice, group.id)) is ErrorCode.NOT_FOUND


def test_group_reorder_uses_category_version(uow: SqlUnitOfWork, alice: int) -> None:
    category = uow.categories.create(alice, "А")
    first, second = uow.groups.create(alice, category.id, "1"), uow.groups.create(alice, category.id, "2")
    version = uow.categories.get(alice, category.id).version

    assert uow.groups.reorder(alice, category.id, [second.id, first.id], version) == version + 1
    assert [g.id for g in uow.groups.list_in_category(alice, category.id)] == [second.id, first.id]
    error = error_of(lambda: uow.groups.reorder(alice, category.id, [first.id, second.id], version))
    assert error.code is ErrorCode.VERSION_CONFLICT
    assert error.details["current"].version == version + 1
    assert code_of(lambda: uow.groups.reorder(alice, category.id, [first.id], version + 1)) is ErrorCode.VALIDATION_ERROR


# --- товары ------------------------------------------------------------------


@pytest.fixture
def shelf(uow: SqlUnitOfWork, alice: int) -> tuple[int, int, int]:
    """Категория с двумя группами: (category_id, group_a, group_b)."""
    category = uow.categories.create(alice, "Компьютер")
    return (
        category.id,
        uow.groups.create(alice, category.id, "Мониторы").id,
        uow.groups.create(alice, category.id, "Клавиатуры").id,
    )


def test_item_create_and_list(uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int]) -> None:
    category_id, group_a, _ = shelf

    grouped = uow.items.create(alice, NewItem(category_id=category_id, group_id=group_a, name=" Монитор  27 ", quantity=2))
    loose = uow.items.create(alice, NewItem(category_id=category_id, name="Кабель", full_name="Кабель HDMI 2 м"))

    assert (grouped.name, grouped.quantity, grouped.in_total, grouped.position) == ("Монитор 27", 2, True, 0)
    assert (loose.group_id, loose.full_name, loose.position) == (None, "Кабель HDMI 2 м", 0)
    assert [i.id for i in uow.items.list_in_category(alice, category_id)] == [loose.id, grouped.id]


def test_item_names_may_repeat(uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int]) -> None:
    category_id = shelf[0]
    uow.items.create(alice, NewItem(category_id=category_id, name="Кабель"))

    assert uow.items.create(alice, NewItem(category_id=category_id, name="Кабель")).position == 1


def test_item_create_rejects_bad_parents(uow: SqlUnitOfWork, alice: int, bob: int, shelf: tuple[int, int, int]) -> None:
    category_id, group_a, _ = shelf
    other = uow.categories.create(alice, "Другая")
    foreign_category = uow.categories.create(bob, "Чужая")
    foreign_group = uow.groups.create(bob, foreign_category.id, "Чужая группа")

    assert code_of(lambda: uow.items.create(alice, NewItem(category_id=foreign_category.id, name="Т"))) is ErrorCode.NOT_FOUND
    assert code_of(
        lambda: uow.items.create(alice, NewItem(category_id=category_id, group_id=foreign_group.id, name="Т"))
    ) is ErrorCode.NOT_FOUND
    error = error_of(lambda: uow.items.create(alice, NewItem(category_id=other.id, group_id=group_a, name="Т")))
    assert error.code is ErrorCode.VALIDATION_ERROR
    assert error.details == {"fields": {"group_id": "other_category"}}


def test_item_create_in_deleted_category_even_with_active_group(
    uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int]
) -> None:
    category_id, group_a, _ = shelf
    uow.categories.delete(alice, category_id, uow.categories.get(alice, category_id).version)

    assert code_of(
        lambda: uow.items.create(alice, NewItem(category_id=category_id, group_id=group_a, name="Т"))
    ) is ErrorCode.NOT_FOUND


@pytest.mark.parametrize(
    ("new", "field", "reason"),
    [
        ({"name": ""}, "name", "empty"),
        ({"name": "я" * 201}, "name", "too_long"),
        ({"full_name": "я" * 501}, "full_name", "too_long"),
        ({"quantity": 0}, "quantity", "out_of_range"),
        ({"quantity": 1000}, "quantity", "out_of_range"),
    ],
)
def test_item_create_validates_fields(
    uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int], new: dict, field: str, reason: str
) -> None:
    values = {"category_id": shelf[0], "name": "Т"} | new

    error = error_of(lambda: uow.items.create(alice, NewItem(**values)))

    assert error.details == {"fields": {field: reason}}


def test_item_update_partial(uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int]) -> None:
    item = uow.items.create(alice, NewItem(category_id=shelf[0], name="Т", full_name="Полное", quantity=2))

    updated = uow.items.update(alice, item.id, item.version, ItemChanges(quantity=5, in_total=False))
    cleared = uow.items.update(alice, item.id, updated.version, ItemChanges(full_name=None))

    assert (updated.name, updated.full_name, updated.quantity, updated.in_total) == ("Т", "Полное", 5, False)
    assert (cleared.full_name, cleared.quantity, cleared.version) == (None, 5, 3)


def test_item_update_stale_version_and_owner(uow: SqlUnitOfWork, alice: int, bob: int, shelf: tuple[int, int, int]) -> None:
    item = uow.items.create(alice, NewItem(category_id=shelf[0], name="Т"))
    current = uow.items.update(alice, item.id, 1, ItemChanges(quantity=2))

    error = error_of(lambda: uow.items.update(alice, item.id, 1, ItemChanges(quantity=3)))
    assert error.code is ErrorCode.VERSION_CONFLICT
    assert error.details["current"] == current
    assert code_of(lambda: uow.items.update(bob, item.id, 2, ItemChanges(quantity=3))) is ErrorCode.NOT_FOUND
    assert code_of(lambda: uow.items.update(alice, item.id, 2, ItemChanges(quantity=0))) is ErrorCode.VALIDATION_ERROR


def test_item_move_between_groups_goes_to_end(uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int]) -> None:
    category_id, group_a, group_b = shelf
    resident = uow.items.create(alice, NewItem(category_id=category_id, group_id=group_b, name="Уже там"))
    item = uow.items.create(alice, NewItem(category_id=category_id, group_id=group_a, name="Т"))
    versions_before = (uow.groups.get(alice, group_a).version, uow.groups.get(alice, group_b).version)

    moved = uow.items.update(alice, item.id, item.version, ItemChanges(group_id=group_b))
    ungrouped = uow.items.update(alice, item.id, moved.version, ItemChanges(group_id=None))

    assert (moved.group_id, moved.position) == (group_b, resident.position + 1)
    assert uow.groups.get(alice, group_a).version == versions_before[0] + 1
    assert uow.groups.get(alice, group_b).version == versions_before[1] + 2
    assert ungrouped.group_id is None


def test_item_delete_restore(uow: SqlUnitOfWork, alice: int, bob: int, shelf: tuple[int, int, int]) -> None:
    item = uow.items.create(alice, NewItem(category_id=shelf[0], group_id=shelf[1], name="Т"))

    assert code_of(lambda: uow.items.delete(bob, item.id, 1)) is ErrorCode.NOT_FOUND
    assert code_of(lambda: uow.items.delete(alice, item.id, 9)) is ErrorCode.VERSION_CONFLICT
    uow.items.delete(alice, item.id, 1)
    assert code_of(lambda: uow.items.get(alice, item.id)) is ErrorCode.NOT_FOUND
    assert code_of(lambda: uow.items.restore(bob, item.id)) is ErrorCode.NOT_FOUND
    assert uow.items.restore(alice, item.id).version == 3
    assert code_of(lambda: uow.items.restore(alice, item.id)) is ErrorCode.NOT_FOUND


def test_item_restore_needs_active_parents(uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int]) -> None:
    category_id, group_a, _ = shelf
    item = uow.items.create(alice, NewItem(category_id=category_id, group_id=group_a, name="Т"))
    uow.items.delete(alice, item.id, 1)
    uow.groups.delete(alice, group_a, uow.groups.get(alice, group_a).version)

    assert code_of(lambda: uow.items.restore(alice, item.id)) is ErrorCode.NOT_FOUND


def test_item_reorder_with_parent_versions(uow: SqlUnitOfWork, alice: int, shelf: tuple[int, int, int]) -> None:
    category_id, group_a, _ = shelf
    loose = [uow.items.create(alice, NewItem(category_id=category_id, name=n)).id for n in ("1", "2")]
    grouped = [uow.items.create(alice, NewItem(category_id=category_id, group_id=group_a, name=n)).id for n in ("3", "4")]
    category_version = uow.categories.get(alice, category_id).version
    group_version = uow.groups.get(alice, group_a).version

    uow.items.reorder(alice, category_id, None, loose[::-1], category_version)
    uow.items.reorder(alice, category_id, group_a, grouped[::-1], group_version)

    assert [i.id for i in uow.items.list_in_category(alice, category_id)] == loose[::-1] + grouped[::-1]
    assert code_of(lambda: uow.items.reorder(alice, category_id, group_a, grouped, group_version)) is (
        ErrorCode.VERSION_CONFLICT
    )
    assert code_of(lambda: uow.items.reorder(alice, category_id, None, loose + grouped, category_version + 1)) is (
        ErrorCode.VALIDATION_ERROR
    )


# --- единица работы ----------------------------------------------------------


@pytest.fixture
def committed_user(engine: Engine) -> int:
    with engine.begin() as connection:
        return add_user(connection)


def test_unit_of_work_commit_persists(engine: Engine, committed_user: int) -> None:
    with SqlUnitOfWork(engine) as work:
        category = work.categories.create(committed_user, "Сохранится")
        work.commit()

    with SqlUnitOfWork(engine) as work:
        assert work.categories.get(committed_user, category.id).name == "Сохранится"


def test_unit_of_work_without_commit_rolls_back(engine: Engine, committed_user: int) -> None:
    with SqlUnitOfWork(engine) as work:
        work.categories.create(committed_user, "Пропадёт")

    with SqlUnitOfWork(engine) as work:
        assert work.categories.list_active(committed_user) == []


def test_unit_of_work_rolls_back_failed_reorder(engine: Engine, committed_user: int) -> None:
    with SqlUnitOfWork(engine) as work:
        a, b = work.categories.create(committed_user, "А"), work.categories.create(committed_user, "Б")
        order_version = work.categories.order_version(committed_user)
        work.commit()

    with pytest.raises(PriceWatcherError), SqlUnitOfWork(engine) as work:
        work.categories.reorder(committed_user, [b.id], order_version)

    with SqlUnitOfWork(engine) as work:
        assert work.categories.order_version(committed_user) == order_version
        assert [c.id for c in work.categories.list_active(committed_user)] == [a.id, b.id]


def test_unit_of_work_misuse(engine: Engine) -> None:
    work = SqlUnitOfWork(engine)
    with pytest.raises(RuntimeError):
        work.commit()
    with work, pytest.raises(RuntimeError):
        work.__enter__()


# --- соответствие интерфейсам ------------------------------------------------


@pytest.mark.parametrize(
    ("protocol", "implementation"),
    [
        (ports.CategoryRepository, SqlCategoryRepository),
        (ports.GroupRepository, SqlGroupRepository),
        (ports.ItemRepository, SqlItemRepository),
    ],
)
def test_repositories_match_protocols(protocol: type, implementation: type) -> None:
    public = [name for name in vars(protocol) if not name.startswith("_")]

    for name in public:
        expected = list(inspect.signature(getattr(protocol, name)).parameters)
        actual = list(inspect.signature(getattr(implementation, name)).parameters)
        assert actual == expected, name


# --- чужой идентификатор для каждого метода ----------------------------------


@pytest.fixture
def alices_records(uow: SqlUnitOfWork, alice: int) -> dict[str, int]:
    category = uow.categories.create(alice, "Её категория")
    group = uow.groups.create(alice, category.id, "Её группа")
    item = uow.items.create(alice, NewItem(category_id=category.id, group_id=group.id, name="Её товар"))
    deleted_category = uow.categories.create(alice, "Удалённая")
    uow.categories.delete(alice, deleted_category.id, deleted_category.version)
    deleted_group = uow.groups.create(alice, category.id, "Удалённая группа")
    uow.groups.delete(alice, deleted_group.id, deleted_group.version)
    deleted_item = uow.items.create(alice, NewItem(category_id=category.id, name="Удалённый"))
    uow.items.delete(alice, deleted_item.id, deleted_item.version)
    return {
        "category": category.id,
        "group": group.id,
        "item": item.id,
        "deleted_category": deleted_category.id,
        "deleted_group": deleted_group.id,
        "deleted_item": deleted_item.id,
    }


FOREIGN_CALLS: dict[str, Callable[[SqlUnitOfWork, int, dict[str, int]], object]] = {
    "categories.get": lambda w, u, r: w.categories.get(u, r["category"]),
    "categories.rename": lambda w, u, r: w.categories.rename(u, r["category"], "Захват", 1),
    "categories.delete": lambda w, u, r: w.categories.delete(u, r["category"], 1),
    "categories.restore": lambda w, u, r: w.categories.restore(u, r["deleted_category"]),
    "categories.order_version": lambda w, u, r: w.categories.order_version(10**12),
    "groups.list_in_category": lambda w, u, r: w.groups.list_in_category(u, r["category"]),
    "groups.get": lambda w, u, r: w.groups.get(u, r["group"]),
    "groups.create": lambda w, u, r: w.groups.create(u, r["category"], "Захват"),
    "groups.rename": lambda w, u, r: w.groups.rename(u, r["group"], "Захват", 1),
    "groups.delete": lambda w, u, r: w.groups.delete(u, r["group"], 1),
    "groups.restore": lambda w, u, r: w.groups.restore(u, r["deleted_group"]),
    "groups.reorder": lambda w, u, r: w.groups.reorder(u, r["category"], [r["group"]], 1),
    "items.list_in_category": lambda w, u, r: w.items.list_in_category(u, r["category"]),
    "items.get": lambda w, u, r: w.items.get(u, r["item"]),
    "items.create": lambda w, u, r: w.items.create(u, NewItem(category_id=r["category"], name="Захват")),
    "items.update": lambda w, u, r: w.items.update(u, r["item"], 1, ItemChanges(quantity=2)),
    "items.delete": lambda w, u, r: w.items.delete(u, r["item"], 1),
    "items.restore": lambda w, u, r: w.items.restore(u, r["deleted_item"]),
    "items.reorder_ungrouped": lambda w, u, r: w.items.reorder(u, r["category"], None, [], 1),
    "items.reorder_grouped": lambda w, u, r: w.items.reorder(u, r["category"], r["group"], [r["item"]], 1),
}


@pytest.mark.parametrize("method", sorted(FOREIGN_CALLS))
def test_foreign_id_is_not_found_for_every_method(
    uow: SqlUnitOfWork, bob: int, alices_records: dict[str, int], method: str
) -> None:
    assert code_of(lambda: FOREIGN_CALLS[method](uow, bob, alices_records)) is ErrorCode.NOT_FOUND


def test_foreign_calls_cover_every_repository_method() -> None:
    covered = {name.split(".")[0] + "." + name.split(".")[1].removesuffix("_ungrouped").removesuffix("_grouped")
               for name in FOREIGN_CALLS}
    expected = {
        f"{kind}.{name}"
        for kind, protocol in (("categories", ports.CategoryRepository), ("groups", ports.GroupRepository), ("items", ports.ItemRepository))
        for name in vars(protocol)
        if not name.startswith("_")
    }
    # categories.create и list_active принимают только user_id — чужой записи там нет;
    # чужие id в categories.reorder дают VALIDATION_ERROR (test_category_reorder_requires_exact_list).
    assert expected - covered == {"categories.create", "categories.list_active", "categories.reorder"}
