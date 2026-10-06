"""Добавление ссылки к товару на настоящей базе: магазин создаётся сам, каталог без дублей, срочное задание."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from concurrency_helpers import overlapped
from sqlalchemy import Engine, text

from pricewatch.application.links import NEW_LINKS_PER_HOUR, add_link
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
CATALOG = LinkCatalog()


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


def user_item(engine: Engine) -> tuple[int, int]:
    with engine.begin() as db:
        user_id = db.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"),
            {"l": f"u{uuid.uuid4().hex[:10]}"},
        ).scalar_one()
        category = db.execute(
            text("INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, 'Дом', 'дом', 0) RETURNING id"), {"u": user_id}
        ).scalar_one()
        item = db.execute(
            text("INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, 'Лампа', 0) RETURNING id"), {"u": user_id, "c": category}
        ).scalar_one()
    return user_id, item


def shop(tag: str | None = None) -> str:
    """Адрес на новом, ещё неизвестном сайте — у каждого теста свой."""
    return f"https://www.shop-{tag or uuid.uuid4().hex[:8]}.example/p/lamp-{uuid.uuid4().hex[:6]}?utm_source=ad"


def add(engine: Engine, user_id: int, item_id: int, url: str, now: datetime = NOW):  # type: ignore[no-untyped-def]
    return add_link(SqlUnitOfWork(engine), CATALOG, user_id, item_id, url, "spb", now)


def query(engine: Engine, sql: str, **params: object) -> list[tuple]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(text(sql), params)]


# --- обычный путь ---


def test_new_site_creates_store_link_binding_and_urgent_task(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    url = shop()

    added = add(engine, user_id, item_id, url)

    (store,) = query(engine, "SELECT parser_key, auto_created, enabled, min_delay_ms, display_name FROM stores WHERE id = :id", id=added.store_id)
    assert store == ("universal", True, True, 2000, url.split("/")[2].removeprefix("www."))
    (link,) = query(engine, "SELECT normalized_url, region_code FROM tracked_links WHERE id = :id", id=added.tracked_link_id)
    assert link == (url.split("?")[0], "spb")  # метка рекламы убрана
    (task,) = query(
        engine, "SELECT t.status, t.priority, r.kind, r.total FROM check_tasks t JOIN parse_runs r ON r.id = t.run_id WHERE t.id = :id", id=added.task_id
    )
    assert task == ("queued", 100, "on_add", 1)
    (binding,) = query(engine, "SELECT item_id, valid_from, valid_to FROM item_links WHERE id = :id", id=added.item_link_id)
    assert binding == (item_id, NOW, None)


def test_known_store_is_not_auto_created_and_link_is_canonical(engine: Engine) -> None:
    user_id, item_id = user_item(engine)

    added = add(engine, user_id, item_id, "https://www.ozon.ru/search/?text=x&product_id=3208684872")

    assert query(engine, "SELECT parser_key, auto_created, has_card_price, display_name FROM stores WHERE id = :id", id=added.store_id) == [
        ("ozon", False, True, "Ozon")
    ]
    assert query(engine, "SELECT normalized_url FROM tracked_links WHERE id = :id", id=added.tracked_link_id) == [
        ("https://www.ozon.ru/product/3208684872/",)
    ]


def test_same_page_from_two_users_is_one_catalog_link_and_one_task(engine: Engine) -> None:
    first_user, first_item = user_item(engine)
    second_user, second_item = user_item(engine)
    url = shop()

    first = add(engine, first_user, first_item, url)
    second = add(engine, second_user, second_item, url + "&yclid=1")

    assert (second.tracked_link_id, second.store_id, second.task_id) == (first.tracked_link_id, first.store_id, first.task_id)
    assert second.item_link_id != first.item_link_id


def test_fresh_price_needs_no_check(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    url = shop()
    first = add(engine, user_id, item_id, url)
    with engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET status = 'done', finished_at = :t WHERE id = :id"), {"t": NOW, "id": first.task_id})
        db.execute(
            text(
                "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, availability, region_code, confirmation, source) "
                "VALUES (:l, :t, :at, 'ok', 99000, 'in_stock', 'spb', 'confirmed', 'site_data')"
            ),
            {"l": first.tracked_link_id, "t": first.task_id, "at": NOW - timedelta(hours=23, minutes=59)},
        )
    other_user, other_item = user_item(engine)

    second = add(engine, other_user, other_item, url)

    assert second.task_id is None


def test_day_old_price_is_checked_again(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    url = shop()
    first = add(engine, user_id, item_id, url)
    with engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET status = 'done', finished_at = :t WHERE id = :id"), {"t": NOW, "id": first.task_id})
        db.execute(
            text(
                "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, availability, region_code, confirmation, source) "
                "VALUES (:l, :t, :at, 'ok', 99000, 'in_stock', 'spb', 'confirmed', 'site_data')"
            ),
            {"l": first.tracked_link_id, "t": first.task_id, "at": NOW - timedelta(days=1, seconds=1)},
        )
    other_user, other_item = user_item(engine)

    assert add(engine, other_user, other_item, url).task_id not in (None, first.task_id)


def test_same_link_again_changes_nothing(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    url = shop()
    first = add(engine, user_id, item_id, url)

    again = add(engine, user_id, item_id, url, NOW + timedelta(minutes=1))

    assert again.item_link_id == first.item_link_id
    assert query(engine, "SELECT count(*) FROM item_links WHERE item_id = :i", i=item_id) == [(1,)]


def test_other_link_of_same_store_replaces_previous_and_keeps_history(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    site = uuid.uuid4().hex[:8]
    first = add(engine, user_id, item_id, shop(site))
    later = NOW + timedelta(hours=1)

    second = add(engine, user_id, item_id, shop(site), later)

    assert second.store_id == first.store_id and second.tracked_link_id != first.tracked_link_id
    rows = query(engine, "SELECT tracked_link_id, valid_from, valid_to FROM item_links WHERE item_id = :i ORDER BY id", i=item_id)
    assert rows == [(first.tracked_link_id, NOW, later), (second.tracked_link_id, later, None)]


def test_store_colors_go_round_the_palette(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    added = add(engine, user_id, item_id, shop())

    (color,) = query(engine, "SELECT color FROM stores WHERE id = :id", id=added.store_id)[0]
    assert color.startswith("store-") and 1 <= int(color.removeprefix("store-")) <= 8


# --- отказы ---


def test_rate_limit_twenty_new_links_per_hour(engine: Engine) -> None:
    user_id, _ = user_item(engine)
    items = [user_item_for(engine, user_id) for _ in range(NEW_LINKS_PER_HOUR + 1)]
    for index, item_id in enumerate(items[:NEW_LINKS_PER_HOUR]):
        add(engine, user_id, item_id, shop(), NOW + timedelta(minutes=index))

    with pytest.raises(PriceWatcherError) as caught:
        add(engine, user_id, items[-1], shop(), NOW + timedelta(minutes=30))

    assert caught.value.code is ErrorCode.RATE_LIMITED
    assert caught.value.details["retry_after_seconds"] == 30 * 60  # первая из 20 выйдет из окна в 13:00
    # Через час после первой — снова можно.
    add(engine, user_id, items[-1], shop(), NOW + timedelta(hours=1))


def user_item_for(engine: Engine, user_id: int) -> int:
    with engine.begin() as db:
        category = db.execute(text("SELECT id FROM categories WHERE user_id = :u LIMIT 1"), {"u": user_id}).scalar_one()
        return db.execute(
            text("INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, 'Ещё', 1) RETURNING id"), {"u": user_id, "c": category}
        ).scalar_one()


def test_someone_elses_item_is_not_found(engine: Engine) -> None:
    _, item_id = user_item(engine)
    stranger, _ = user_item(engine)

    with pytest.raises(PriceWatcherError) as caught:
        add(engine, stranger, item_id, shop())

    assert caught.value.code is ErrorCode.NOT_FOUND


def test_deleted_item_is_not_found(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    with engine.begin() as db:
        db.execute(text("UPDATE items SET deleted_at = :t WHERE id = :id"), {"t": NOW, "id": item_id})

    with pytest.raises(PriceWatcherError) as caught:
        add(engine, user_id, item_id, shop())

    assert caught.value.code is ErrorCode.NOT_FOUND


def test_store_disabled_by_admin_is_refused(engine: Engine) -> None:
    user_id, item_id = user_item(engine)
    site = uuid.uuid4().hex[:8]
    add(engine, user_id, item_id, shop(site))
    with engine.begin() as db:
        db.execute(text("UPDATE stores SET enabled = false WHERE canonical_host = :h"), {"h": f"shop-{site}.example"})
    other_user, other_item = user_item(engine)

    with pytest.raises(PriceWatcherError) as caught:
        add(engine, other_user, other_item, shop(site))

    assert caught.value.code is ErrorCode.STORE_NOT_ALLOWED


@pytest.mark.parametrize("url", ["http://shop.example/p", "https://127.0.0.1/p", "https://www.ozon.ru/category/x-1/", "не ссылка"])
def test_bad_address_is_refused_before_touching_database(engine: Engine, url: str) -> None:
    user_id, item_id = user_item(engine)

    with pytest.raises(PriceWatcherError) as caught:
        add(engine, user_id, item_id, url)

    assert caught.value.code is ErrorCode.INVALID_URL
    assert query(engine, "SELECT count(*) FROM item_links WHERE item_id = :i", i=item_id) == [(0,)]


# --- одновременность ---


class HeldWork:
    """Уже открытая транзакция помощника overlapped: сценарий не открывает и не фиксирует её сам."""

    def __init__(self, work: SqlUnitOfWork) -> None:
        self._work = work

    def __getattr__(self, name: str) -> object:
        return getattr(self._work, name)

    def __enter__(self) -> "HeldWork":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def commit(self) -> None:
        return None


def test_two_users_add_same_new_page_at_the_same_time(engine: Engine) -> None:
    first_user, first_item = user_item(engine)
    second_user, second_item = user_item(engine)
    url = shop()

    first, second = overlapped(
        engine,
        lambda work: add_link(HeldWork(work), CATALOG, first_user, first_item, url, "spb", NOW),  # type: ignore[arg-type]
        lambda work: add_link(HeldWork(work), CATALOG, second_user, second_item, url, "spb", NOW),  # type: ignore[arg-type]
    )

    assert first.error is None and second.error is None
    assert first.value.tracked_link_id == second.value.tracked_link_id  # type: ignore[union-attr]
    assert first.value.task_id == second.value.task_id  # type: ignore[union-attr]
    link_id = first.value.tracked_link_id  # type: ignore[union-attr]
    assert query(engine, "SELECT count(*) FROM tracked_links WHERE id = :l", l=link_id) == [(1,)]
    assert query(engine, "SELECT count(*) FROM check_tasks WHERE tracked_link_id = :l", l=link_id) == [(1,)]
    assert query(engine, "SELECT count(*) FROM parse_runs r JOIN check_tasks t ON t.run_id = r.id WHERE t.tracked_link_id = :l", l=link_id) == [(1,)]

