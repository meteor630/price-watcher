"""Данные заблокированного пользователя мягко удаляются через 7 суток; удалённое стирается через 30 дней;
общие ссылки и история цен остаются.

У модуля своя база: сценарий обходит всех пользователей базы, чужие строки других модулей мешали бы.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from concurrency_helpers import lock_waiters, wait_for
from sqlalchemy import Engine, text
from test_runs import bind, item, link, sql, store, user

from pricewatch.application.ports import PurgedCounts
from pricewatch.application.retention import delete_blocked_users_data, purge_deleted
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
WEEK = timedelta(days=7)
KEPT = {"categories": 2, "groups": 1, "items": 2}  # помощник item() заводит категорию на каждый товар


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores CASCADE"))


def owner_with_data(engine: Engine, blocked_at: datetime | None) -> tuple[int, int]:
    """Пользователь с двумя категориями, группой и двумя товарами (один куплен); ссылка товара — в общем каталоге."""
    user_id = user(engine)
    item_id = item(engine, user_id)
    category = sql(engine, "SELECT category_id FROM items WHERE id = :i", i=item_id)[0][0]
    group = sql(
        engine, "INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, 'Свет', 'свет', 0) RETURNING id", u=user_id, c=category
    )[0][0]
    sql(engine, "UPDATE items SET group_id = :g WHERE id = :i", g=group, i=item_id)
    item(engine, user_id, "purchased", NOW - timedelta(days=20))
    bind(engine, user_id, item_id, link(engine, store(engine)))
    sql(engine, "UPDATE users SET disabled_at = :d WHERE id = :u", d=blocked_at, u=user_id)
    return user_id, item_id


def alive(engine: Engine, user_id: int) -> dict[str, int]:
    return {
        table: sql(engine, f"SELECT count(*) FROM {table} WHERE user_id = :u AND deleted_at IS NULL", u=user_id)[0][0]  # noqa: S608 — имя таблицы из списка теста
        for table in ("categories", "groups", "items")
    }


def run(engine: Engine, at: datetime = NOW) -> int:
    return delete_blocked_users_data(SqlUnitOfWork(engine), at)


def test_data_of_user_blocked_seven_days_ago_is_soft_deleted(engine: Engine) -> None:
    user_id, item_id = owner_with_data(engine, NOW - WEEK)
    order_version = sql(engine, "SELECT categories_order_version FROM users WHERE id = :u", u=user_id)[0][0]

    assert run(engine) == 1

    assert alive(engine, user_id) == {"categories": 0, "groups": 0, "items": 0}
    assert sql(engine, "SELECT deleted_at, version FROM items WHERE id = :i", i=item_id) == [(NOW, 2)]
    assert sql(engine, "SELECT disabled_at IS NOT NULL, categories_order_version FROM users WHERE id = :u", u=user_id) == [(True, order_version + 1)]


def test_less_than_seven_days_keeps_data(engine: Engine) -> None:
    user_id, _ = owner_with_data(engine, NOW - WEEK + timedelta(seconds=1))

    assert run(engine) == 0
    assert alive(engine, user_id) == KEPT


def test_active_user_keeps_data(engine: Engine) -> None:
    user_id, _ = owner_with_data(engine, None)

    assert run(engine) == 0
    assert alive(engine, user_id) == KEPT


def test_reblocking_starts_count_again(engine: Engine) -> None:
    user_id, _ = owner_with_data(engine, NOW - timedelta(days=10))
    sql(engine, "UPDATE users SET disabled_at = NULL WHERE id = :u", u=user_id)  # разблокировали
    sql(engine, "UPDATE users SET disabled_at = :d WHERE id = :u", d=NOW - timedelta(days=2), u=user_id)  # и снова заблокировали

    assert run(engine) == 0
    assert run(engine, NOW + timedelta(days=5)) == 1


def test_shared_catalog_and_other_users_stay(engine: Engine) -> None:
    blocked, blocked_item = owner_with_data(engine, NOW - WEEK)
    other, other_item = owner_with_data(engine, None)
    shared = sql(engine, "SELECT tracked_link_id FROM item_links WHERE item_id = :i", i=blocked_item)[0][0]
    bind(engine, other, other_item, shared)

    run(engine)

    assert alive(engine, other) == KEPT
    assert sql(engine, "SELECT count(*) FROM tracked_links WHERE id = :l", l=shared) == [(1,)]
    assert sql(engine, "SELECT count(*) FROM item_links WHERE tracked_link_id = :l", l=shared) == [(2,)]


def test_earlier_deletions_keep_their_time_and_second_pass_changes_nothing(engine: Engine) -> None:
    user_id, item_id = owner_with_data(engine, NOW - WEEK)
    earlier = NOW - timedelta(days=3)
    sql(engine, "UPDATE items SET deleted_at = :d WHERE id = :i", d=earlier, i=item_id)

    assert run(engine) == 1
    assert run(engine, NOW + timedelta(hours=1)) == 0
    assert sql(engine, "SELECT deleted_at FROM items WHERE id = :i", i=item_id) == [(earlier,)]


def test_user_being_changed_by_admin_is_left_for_next_pass(engine: Engine) -> None:
    user_id, _ = owner_with_data(engine, NOW - WEEK)

    with engine.begin() as admin:
        admin.execute(text("SELECT id FROM users WHERE id = :u FOR UPDATE"), {"u": user_id})
        assert run(engine) == 0

    assert run(engine) == 1



# --- окончательная очистка через 30 дней ---

MONTH = timedelta(days=30)


def purge(engine: Engine, at: datetime = NOW) -> PurgedCounts:
    return purge_deleted(SqlUnitOfWork(engine), at)


def exists_row(engine: Engine, table: str, row_id: int) -> bool:
    return sql(engine, f"SELECT count(*) FROM {table} WHERE id = :i", i=row_id) == [(1,)]  # noqa: S608 — имя таблицы из теста


def test_item_deleted_thirty_days_ago_is_purged_with_its_links(engine: Engine) -> None:
    user_id, item_id = owner_with_data(engine, None)
    link_id = sql(engine, "SELECT tracked_link_id FROM item_links WHERE item_id = :i", i=item_id)[0][0]
    sql(engine, "UPDATE items SET deleted_at = :d WHERE id = :i", d=NOW - MONTH, i=item_id)

    assert purge(engine) == PurgedCounts(items=1)

    assert not exists_row(engine, "items", item_id)
    assert sql(engine, "SELECT count(*) FROM item_links WHERE item_id = :i", i=item_id) == [(0,)]
    assert exists_row(engine, "tracked_links", link_id)  # каталог общий
    assert alive(engine, user_id) == {"categories": 2, "groups": 1, "items": 1}


def test_younger_than_thirty_days_stays(engine: Engine) -> None:
    _, item_id = owner_with_data(engine, None)
    sql(engine, "UPDATE items SET deleted_at = :d WHERE id = :i", d=NOW - MONTH + timedelta(seconds=1), i=item_id)

    assert purge(engine) == PurgedCounts()
    assert exists_row(engine, "items", item_id)


def test_deleted_group_takes_its_items_but_not_category(engine: Engine) -> None:
    _, item_id = owner_with_data(engine, None)
    group, category = sql(engine, "SELECT group_id, category_id FROM items WHERE id = :i", i=item_id)[0]
    sql(engine, "UPDATE groups SET deleted_at = :d WHERE id = :g", d=NOW - MONTH, g=group)  # товар внутри не удалён отдельно

    assert purge(engine) == PurgedCounts(groups=1, items=1)
    assert not exists_row(engine, "items", item_id)
    assert exists_row(engine, "categories", category)


def test_deleted_category_takes_groups_and_items(engine: Engine) -> None:
    _, item_id = owner_with_data(engine, None)
    category = sql(engine, "SELECT category_id FROM items WHERE id = :i", i=item_id)[0][0]
    sql(engine, "UPDATE categories SET deleted_at = :d WHERE id = :c", d=NOW - MONTH, c=category)

    assert purge(engine) == PurgedCounts(categories=1, groups=1, items=1)
    assert not exists_row(engine, "categories", category)


def test_blocked_user_data_is_gone_after_seven_plus_thirty_days(engine: Engine) -> None:
    user_id, _ = owner_with_data(engine, NOW - WEEK)
    run(engine)

    assert purge(engine, NOW + MONTH - timedelta(seconds=1)) == PurgedCounts()
    assert purge(engine, NOW + MONTH) == PurgedCounts(categories=2, groups=1, items=2)
    assert sql(engine, "SELECT count(*) FROM users WHERE id = :u", u=user_id) == [(1,)]  # учётная запись остаётся


def test_restored_meanwhile_is_not_purged(engine: Engine) -> None:
    _, item_id = owner_with_data(engine, None)
    sql(engine, "UPDATE items SET deleted_at = :d WHERE id = :i", d=NOW - MONTH, i=item_id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as restoring:
            restoring.execute(text("UPDATE items SET deleted_at = NULL WHERE id = :i"), {"i": item_id})
            purging = pool.submit(purge, engine)
            wait_for(lambda: lock_waiters(engine) > 0, "очистка ждёт восстановление")
        # Восстановление зафиксировано раньше, чем очистка перепроверила строку.
        assert purging.result(10) == PurgedCounts()

    assert exists_row(engine, "items", item_id)
