"""Схема сбора цен и очереди (миграция 0003): гарантии раздела 8 держит сама база.

- не больше одного активного планового или ручного запуска; плановый слот — один раз;
- не больше одного активного задания на ссылку; повтор результата не создаёт вторую строку истории;
- у товара одна действующая ссылка на магазин; привязать ссылку к чужому товару нельзя;
- сбой проверки — без цены; успешная проверка «в наличии» — с ценой.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from pricewatch.persistence.database import create_db_engine

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
HASH = "a" * 64


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Connection]:
    """Каждый тест — в своей транзакции, которая откатывается: тесты не видят данные друг друга."""
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


def scalar(db: Connection, sql: str, **params: object) -> int:
    return db.execute(text(sql), params).scalar_one()


def store(db: Connection, host: str | None = None) -> int:
    return scalar(
        db,
        "INSERT INTO stores (canonical_host, display_name, color, parser_key, auto_created, min_delay_ms) "
        "VALUES (:h, 'Магазин', 'store-1', 'universal', true, 2000) RETURNING id",
        h=host or f"s{uuid.uuid4().hex[:10]}.example",
    )


def link(db: Connection, store_id: int, url: str | None = None) -> int:
    return scalar(
        db,
        "INSERT INTO tracked_links (store_id, normalized_url, region_code) VALUES (:s, :u, 'spb') RETURNING id",
        s=store_id,
        u=url or f"https://shop.example/p/{uuid.uuid4().hex}",
    )


def user_with_item(db: Connection) -> tuple[int, int]:
    user_id = scalar(
        db,
        "INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id",
        l=f"u{uuid.uuid4().hex[:10]}",
    )
    category = scalar(
        db, "INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, 'Дом', 'дом', 0) RETURNING id", u=user_id
    )
    item = scalar(
        db, "INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, 'Лампа', 0) RETURNING id", u=user_id, c=category
    )
    return user_id, item


def run(db: Connection, kind: str = "manual", status: str = "queued", slot: str | None = None) -> int:
    requested_by = None
    if kind == "manual":
        requested_by, _ = user_with_item(db)
    return scalar(
        db,
        "INSERT INTO parse_runs (kind, slot_key, status, requested_by) VALUES (:k, :s, :st, :r) RETURNING id",
        k=kind, s=slot, st=status, r=requested_by,
    )


def task(db: Connection, run_id: int, link_id: int, status: str = "queued") -> int:
    finished = NOW if status in ("done", "failed", "cancelled") else None
    return scalar(
        db,
        "INSERT INTO check_tasks (run_id, tracked_link_id, status, finished_at) VALUES (:r, :l, :s, :f) RETURNING id",
        r=run_id, l=link_id, s=status, f=finished,
    )


def refused(db: Connection, sql: str, **params: object) -> None:
    savepoint = db.begin_nested()
    with pytest.raises(IntegrityError):
        db.execute(text(sql), params)
    savepoint.rollback()


# --- запуски ---


def test_one_active_manual_or_scheduled_run(db: Connection) -> None:
    run(db, "manual")

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            run(db, "scheduled", slot="2026-10-06T09:00")


def test_finished_run_does_not_block_new_one(db: Connection) -> None:
    first = run(db, "manual")
    db.execute(text("UPDATE parse_runs SET status = 'finished', finished_at = :t WHERE id = :id"), {"t": NOW, "id": first})

    run(db, "manual")


def test_on_add_runs_do_not_count_as_active_run(db: Connection) -> None:
    run(db, "manual")
    run(db, "on_add")
    run(db, "on_add")


def test_scheduled_slot_only_once(db: Connection) -> None:
    first = run(db, "scheduled", slot="2026-10-06T09:00")
    db.execute(text("UPDATE parse_runs SET status = 'finished', finished_at = :t WHERE id = :id"), {"t": NOW, "id": first})

    refused(db, "INSERT INTO parse_runs (kind, slot_key, status) VALUES ('scheduled', '2026-10-06T09:00', 'queued')")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO parse_runs (kind, status) VALUES ('scheduled', 'queued')",  # плановый без слота
        "INSERT INTO parse_runs (kind, slot_key, status) VALUES ('on_add', 'x', 'queued')",  # слот не у планового
        "INSERT INTO parse_runs (kind, status) VALUES ('manual', 'queued')",  # ручной без администратора
        "INSERT INTO parse_runs (kind, status) VALUES ('on_add', 'finished')",  # завершён без времени
        "INSERT INTO parse_runs (kind, status, total, done) VALUES ('on_add', 'queued', 1, 2)",
        "INSERT INTO parse_runs (kind, status) VALUES ('nightly', 'queued')",
    ],
)
def test_bad_runs_refused(db: Connection, sql: str) -> None:
    refused(db, sql)


# --- задания ---


def test_one_active_task_per_link(db: Connection) -> None:
    link_id = link(db, store(db))
    run_id = run(db, "on_add")
    task(db, run_id, link_id)

    refused(db, "INSERT INTO check_tasks (run_id, tracked_link_id) VALUES (:r, :l)", r=run_id, l=link_id)


def test_finished_task_does_not_block_new_one(db: Connection) -> None:
    link_id = link(db, store(db))
    run_id = run(db, "on_add")
    task(db, run_id, link_id, status="done")

    task(db, run_id, link_id)


def test_lease_needs_worker_and_deadline(db: Connection) -> None:
    link_id = link(db, store(db))
    run_id = run(db, "on_add")

    refused(db, "INSERT INTO check_tasks (run_id, tracked_link_id, status) VALUES (:r, :l, 'leased')", r=run_id, l=link_id)


def test_attempts_are_limited(db: Connection) -> None:
    link_id = link(db, store(db))

    refused(db, "INSERT INTO check_tasks (run_id, tracked_link_id, attempt) VALUES (:r, :l, 4)", r=run(db, "on_add"), l=link_id)


# --- наблюдения ---


def observation(db: Connection, task_id: int, link_id: int, **values: object) -> None:
    row = {"status": "ok", "price": 100_000, "card": None, "availability": "in_stock", "confirmation": "confirmed", "source": "site_data"} | values
    db.execute(
        text(
            "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, card_price_minor, "
            "availability, region_code, confirmation, source) VALUES (:l, :t, :at, :status, :price, :card, :availability, 'spb', "
            ":confirmation, :source)"
        ),
        {"l": link_id, "t": task_id, "at": NOW} | row,
    )


def test_repeated_result_does_not_create_second_row(db: Connection) -> None:
    link_id = link(db, store(db))
    task_id = task(db, run(db, "on_add"), link_id)
    observation(db, task_id, link_id)

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            observation(db, task_id, link_id)


@pytest.mark.parametrize(
    "values",
    [
        {"status": "TIMEOUT"},  # сбой с ценой
        {"status": "TIMEOUT", "price": None, "confirmation": "pending", "source": None},  # сбой не ждёт решения
        {"price": None},  # успех в наличии без цены
        {"price": 0},
        {"card": -1},
        {"status": "SOMETHING"},
        {"availability": "maybe"},
        {"source": "guess"},
    ],
)
def test_bad_observations_refused(db: Connection, values: dict) -> None:
    link_id = link(db, store(db))
    task_id = task(db, run(db, "on_add"), link_id)

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            observation(db, task_id, link_id, **values)


def test_failure_and_out_of_stock_without_price_are_fine(db: Connection) -> None:
    link_id = link(db, store(db))
    run_id = run(db, "on_add")
    observation(db, task(db, run_id, link_id, status="done"), link_id, status="CAPTCHA", price=None, source=None, availability="unknown")
    observation(db, task(db, run_id, link_id), link_id, price=None, availability="out_of_stock")


# --- ссылки и привязки ---


def test_same_page_once_in_catalog(db: Connection) -> None:
    store_id = store(db)
    link(db, store_id, "https://shop.example/p/1")

    refused(db, "INSERT INTO tracked_links (store_id, normalized_url, region_code) VALUES (:s, 'https://shop.example/p/1', 'spb')", s=store_id)


def test_store_host_once(db: Connection) -> None:
    store(db, "once.example")

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            store(db, "once.example")


def bind(db: Connection, user_id: int, item_id: int, link_id: int, store_id: int, valid_to: datetime | None = None) -> None:
    db.execute(
        text("INSERT INTO item_links (user_id, item_id, tracked_link_id, store_id, valid_from, valid_to) VALUES (:u, :i, :l, :s, :f, :t)"),
        {"u": user_id, "i": item_id, "l": link_id, "s": store_id, "f": NOW - timedelta(days=1), "t": valid_to},
    )


def test_one_active_link_per_item_and_store(db: Connection) -> None:
    user_id, item_id = user_with_item(db)
    store_id = store(db)
    bind(db, user_id, item_id, link(db, store_id), store_id, valid_to=NOW)  # закрытая привязка не мешает
    bind(db, user_id, item_id, link(db, store_id), store_id)

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            bind(db, user_id, item_id, link(db, store_id), store_id)


def test_link_cannot_be_bound_to_someone_elses_item(db: Connection) -> None:
    _, item_id = user_with_item(db)
    stranger, _ = user_with_item(db)
    store_id = store(db)

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            bind(db, stranger, item_id, link(db, store_id), store_id)


def test_binding_range_must_be_positive(db: Connection) -> None:
    user_id, item_id = user_with_item(db)
    store_id = store(db)

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            bind(db, user_id, item_id, link(db, store_id), store_id, valid_to=NOW - timedelta(days=2))


# --- магазины, worker, товары ---


@pytest.mark.parametrize(
    ("column", "value"),
    [("canonical_host", "Shop.Example"), ("color", "store-9"), ("fetch_method", "ftp"), ("min_delay_ms", -1), ("parser_key", "Ozon!")],
)
def test_bad_store_values_refused(db: Connection, column: str, value: object) -> None:
    store_id = store(db)

    refused(db, f"UPDATE stores SET {column} = :v WHERE id = :id", v=value, id=store_id)


def test_pause_needs_reason(db: Connection) -> None:
    refused(db, "UPDATE stores SET paused_until = :t WHERE id = :id", t=NOW, id=store(db))


@pytest.mark.parametrize(
    ("token", "next_token"),
    [("plain-token", None), (HASH, HASH), (HASH, "not-a-hash")],
)
def test_worker_tokens_are_hashes_and_differ(db: Connection, token: str, next_token: str | None) -> None:
    refused(db, "INSERT INTO workers (name, token_hash, next_token_hash) VALUES ('w', :t, :n)", t=token, n=next_token)


def test_purchase_date_only_for_purchased_item(db: Connection) -> None:
    _, item_id = user_with_item(db)

    refused(db, "UPDATE items SET purchased_at = :t WHERE id = :id", t=NOW, id=item_id)
    refused(db, "UPDATE items SET state = 'purchased' WHERE id = :id", id=item_id)
    refused(db, "UPDATE items SET state = 'lost' WHERE id = :id", id=item_id)
    db.execute(text("UPDATE items SET state = 'purchased', purchased_at = :t WHERE id = :id"), {"t": NOW, "id": item_id})
