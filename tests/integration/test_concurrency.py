"""Параллельные изменения на настоящих соединениях: ровно один успех, второй получает понятную ошибку.

Пересечение транзакций задаётся явно: первая выполняет операцию и держит транзакцию,
вторая в другом потоке упирается в блокировку (это проверяется по pg_stat_activity),
и только потом первая фиксируется. Так проверяется именно одновременный случай,
а не последовательный, который получился бы при удачном расписании потоков.
"""

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from concurrency_helpers import WAIT_S, Outcome, attempt, committed, overlapped
from sqlalchemy import Engine, text

from pricewatch.application.ports import ItemChanges, NewItem
from pricewatch.domain.errors import ErrorCode
from pricewatch.persistence.database import create_db_engine


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def user_id(engine: Engine) -> int:
    with engine.begin() as connection:
        return connection.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"),
            {"l": f"c_{uuid.uuid4().hex[:12]}"},
        ).scalar_one()


def test_two_updates_of_same_version(engine: Engine, user_id: int) -> None:
    category = committed(engine, lambda w: w.categories.create(user_id, "А"))
    item = committed(engine, lambda w: w.items.create(user_id, NewItem(category_id=category.id, name="Т")))

    first, second = overlapped(
        engine,
        lambda w: w.items.update(user_id, item.id, item.version, ItemChanges(quantity=2)),
        lambda w: w.items.update(user_id, item.id, item.version, ItemChanges(quantity=3)),
    )

    assert first.value.quantity == 2
    assert second.error is ErrorCode.VERSION_CONFLICT
    assert committed(engine, lambda w: w.items.get(user_id, item.id)).quantity == 2


def test_two_renames_of_same_version(engine: Engine, user_id: int) -> None:
    category = committed(engine, lambda w: w.categories.create(user_id, "А"))

    first, second = overlapped(
        engine,
        lambda w: w.categories.rename(user_id, category.id, "Первое", category.version),
        lambda w: w.categories.rename(user_id, category.id, "Второе", category.version),
    )

    assert first.value.name == "Первое"
    assert second.error is ErrorCode.VERSION_CONFLICT


def test_two_reorders_of_same_order_version(engine: Engine, user_id: int) -> None:
    a = committed(engine, lambda w: w.categories.create(user_id, "А"))
    b = committed(engine, lambda w: w.categories.create(user_id, "Б"))
    version = committed(engine, lambda w: w.categories.order_version(user_id))

    first, second = overlapped(
        engine,
        lambda w: w.categories.reorder(user_id, [b.id, a.id], version),
        lambda w: w.categories.reorder(user_id, [a.id, b.id], version),
    )

    assert first.value == version + 1
    assert second.error is ErrorCode.VERSION_CONFLICT
    assert [c.id for c in committed(engine, lambda w: w.categories.list_active(user_id))] == [b.id, a.id]


def test_two_creates_of_same_name(engine: Engine, user_id: int) -> None:
    first, second = overlapped(
        engine,
        lambda w: w.categories.create(user_id, "Компьютер"),
        lambda w: w.categories.create(user_id, "КОМПЬЮТЕР"),
    )

    assert first.value.name == "Компьютер"
    assert second.error is ErrorCode.NAME_TAKEN
    assert len(committed(engine, lambda w: w.categories.list_active(user_id))) == 1


def test_two_creates_of_different_names_get_distinct_positions(engine: Engine, user_id: int) -> None:
    first, second = overlapped(
        engine,
        lambda w: w.categories.create(user_id, "Первая"),
        lambda w: w.categories.create(user_id, "Вторая"),
    )

    assert (first.value.position, second.value.position) == (0, 1)


def test_two_item_creates_in_same_group_get_distinct_positions(engine: Engine, user_id: int) -> None:
    category = committed(engine, lambda w: w.categories.create(user_id, "А"))
    group = committed(engine, lambda w: w.groups.create(user_id, category.id, "Г"))
    new = NewItem(category_id=category.id, group_id=group.id, name="Т")

    first, second = overlapped(engine, lambda w: w.items.create(user_id, new), lambda w: w.items.create(user_id, new))

    assert {first.value.position, second.value.position} == {0, 1}


def test_delete_while_rename_in_progress(engine: Engine, user_id: int) -> None:
    category = committed(engine, lambda w: w.categories.create(user_id, "А"))

    first, second = overlapped(
        engine,
        lambda w: w.categories.rename(user_id, category.id, "Б", category.version),
        lambda w: w.categories.delete(user_id, category.id, category.version),
    )

    assert first.value.name == "Б"
    assert second.error is ErrorCode.VERSION_CONFLICT
    assert committed(engine, lambda w: w.categories.get(user_id, category.id)).name == "Б"


def test_many_simultaneous_updates_one_wins(engine: Engine, user_id: int) -> None:
    category = committed(engine, lambda w: w.categories.create(user_id, "А"))
    item = committed(engine, lambda w: w.items.create(user_id, NewItem(category_id=category.id, name="Т")))
    start = threading.Barrier(10)

    def update(quantity: int) -> Outcome:
        start.wait(WAIT_S)
        return attempt(engine, lambda w: w.items.update(user_id, item.id, item.version, ItemChanges(quantity=quantity)))

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(update, range(1, 11)))

    winners = [o for o in outcomes if o.error is None]
    assert len(winners) == 1
    assert all(o.error is ErrorCode.VERSION_CONFLICT for o in outcomes if o.error is not None)
    assert committed(engine, lambda w: w.items.get(user_id, item.id)).quantity == winners[0].value.quantity
