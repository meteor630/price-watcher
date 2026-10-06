"""Протокол worker на настоящей базе: токены, аренда заданий, результат в историю цен."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.links import add_link
from pricewatch.application.worker_protocol import (
    LEASE,
    WorkerResult,
    authenticate_worker,
    claim_task,
    create_worker,
    extend_lease,
    rotate_worker_token,
    submit_result,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"worker-protocol-key-" + b"k" * 20
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
OK = WorkerResult("ok", 100_000, None, "in_stock", "Лампа настольная", "site_data", "Магазин Ламп", "http", 1200)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    with db_engine.begin() as db:
        # Чистая очередь: тесты модуля не видят заданий из других модулей.
        db.execute(text("UPDATE check_tasks SET status = 'cancelled', lease_until = NULL, finished_at = now() WHERE status IN ('queued', 'leased')"))
    yield db_engine
    db_engine.dispose()


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


def worker(engine: Engine) -> int:
    token = create_worker(uow(engine), KEY, f"w-{uuid.uuid4().hex[:8]}", NOW)
    return authenticate_worker(uow(engine), KEY, token, NOW)


def add(engine: Engine, host: str | None = None, path: str | None = None):  # type: ignore[no-untyped-def]
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
    host = host or f"shop-{uuid.uuid4().hex[:8]}.example"
    url = f"https://{host}/{path or uuid.uuid4().hex[:8]}"
    return add_link(uow(engine), LinkCatalog(), user_id, item, url, "spb", NOW)


def query(engine: Engine, sql: str, **params: object) -> list[tuple]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(text(sql), params)]


def drain(engine: Engine, worker_id: int, now: datetime = NOW) -> None:
    """Забрать и закрыть всё, что осталось в очереди от предыдущих шагов теста."""
    while (task := claim_task(uow(engine), worker_id, now)) is not None:
        submit_result(uow(engine), worker_id, task.task_id, WorkerResult("TIMEOUT"), now)


# --- токены ---


def test_unknown_or_missing_token_is_refused(engine: Engine) -> None:
    for token in (None, "", "made-up"):
        with pytest.raises(PriceWatcherError) as caught:
            authenticate_worker(uow(engine), KEY, token, NOW)
        assert caught.value.code is ErrorCode.AUTH_REQUIRED


def test_token_is_stored_only_as_hash(engine: Engine) -> None:
    name = f"w-{uuid.uuid4().hex[:8]}"
    token = create_worker(uow(engine), KEY, name, NOW)

    (stored,) = query(engine, "SELECT token_hash FROM workers WHERE name = :n", n=name)[0]
    assert stored != token and len(stored) == 64


def test_rotation_old_and_new_both_work_until_new_is_used(engine: Engine) -> None:
    name = f"w-{uuid.uuid4().hex[:8]}"
    old = create_worker(uow(engine), KEY, name, NOW)
    new = rotate_worker_token(uow(engine), KEY, name)

    worker_id = authenticate_worker(uow(engine), KEY, old, NOW)
    assert authenticate_worker(uow(engine), KEY, new, NOW) == worker_id
    # Первый вход с новым токеном гасит старый.
    with pytest.raises(PriceWatcherError):
        authenticate_worker(uow(engine), KEY, old, NOW)
    assert authenticate_worker(uow(engine), KEY, new, NOW) == worker_id


def test_disabled_worker_is_refused(engine: Engine) -> None:
    name = f"w-{uuid.uuid4().hex[:8]}"
    token = create_worker(uow(engine), KEY, name, NOW)
    with engine.begin() as db:
        db.execute(text("UPDATE workers SET disabled_at = :t WHERE name = :n"), {"t": NOW, "n": name})

    with pytest.raises(PriceWatcherError):
        authenticate_worker(uow(engine), KEY, token, NOW)


def test_worker_name_taken(engine: Engine) -> None:
    name = f"w-{uuid.uuid4().hex[:8]}"
    create_worker(uow(engine), KEY, name, NOW)

    with pytest.raises(PriceWatcherError) as caught:
        create_worker(uow(engine), KEY, name, NOW)

    assert caught.value.code is ErrorCode.NAME_TAKEN


def test_rotation_of_unknown_worker(engine: Engine) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        rotate_worker_token(uow(engine), KEY, "no-such-worker")

    assert caught.value.code is ErrorCode.NOT_FOUND


# --- аренда ---


def test_claim_gives_task_with_everything_worker_needs(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    added = add(engine)

    task = claim_task(uow(engine), worker_id, NOW)

    assert task is not None and task.task_id == added.task_id
    assert (task.region_code, task.parser_key, task.fetch_method, task.min_delay_ms, task.lease_until) == (
        "spb", "universal", None, 2000, NOW + LEASE
    )
    assert query(engine, "SELECT status, attempt, worker_id FROM check_tasks WHERE id = :t", t=task.task_id) == [("leased", 1, worker_id)]
    assert query(engine, "SELECT r.status FROM parse_runs r JOIN check_tasks t ON t.run_id = r.id WHERE t.id = :t", t=task.task_id) == [("running",)]


def test_one_task_per_store_at_a_time(engine: Engine) -> None:
    first_worker, second_worker = worker(engine), worker(engine)
    drain(engine, first_worker)
    host = f"shop-{uuid.uuid4().hex[:8]}.example"
    add(engine, host)
    add(engine, host)

    first = claim_task(uow(engine), first_worker, NOW)

    assert first is not None
    assert claim_task(uow(engine), second_worker, NOW) is None  # второе задание того же магазина ждёт
    submit_result(uow(engine), first_worker, first.task_id, OK, NOW)
    assert claim_task(uow(engine), second_worker, NOW) is not None


def test_paused_and_disabled_stores_are_skipped(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    paused, disabled = add(engine), add(engine)
    with engine.begin() as db:
        db.execute(text("UPDATE stores SET paused_until = :t, pause_reason = 'BLOCKED', pause_level = 1 WHERE id = :s"), {"t": NOW + timedelta(minutes=15), "s": paused.store_id})
        db.execute(text("UPDATE stores SET enabled = false WHERE id = :s"), {"s": disabled.store_id})

    assert claim_task(uow(engine), worker_id, NOW) is None
    # Пауза кончилась — задания магазина снова выдаются.
    task = claim_task(uow(engine), worker_id, NOW + timedelta(minutes=15))
    assert task is not None and task.task_id == paused.task_id


def test_urgent_task_first(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    first = add(engine)
    urgent = add(engine)
    with engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET priority = 0 WHERE id = :t"), {"t": first.task_id})

    assert claim_task(uow(engine), worker_id, NOW).task_id == urgent.task_id  # type: ignore[union-attr]


def test_heartbeat_extends_lease_only_for_own_live_task(engine: Engine) -> None:
    worker_id, stranger = worker(engine), worker(engine)
    drain(engine, worker_id)
    add(engine)
    task = claim_task(uow(engine), worker_id, NOW)
    assert task is not None

    assert extend_lease(uow(engine), worker_id, task.task_id, NOW + timedelta(minutes=1)) == NOW + timedelta(minutes=1) + LEASE
    for who, when in ((stranger, NOW), (worker_id, NOW + timedelta(minutes=4))):
        with pytest.raises(PriceWatcherError) as caught:
            extend_lease(uow(engine), who, task.task_id, when)
        assert caught.value.code is ErrorCode.NOT_FOUND


# --- результат ---


def test_result_goes_to_history_and_closes_task_and_run(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    added = add(engine)
    task = claim_task(uow(engine), worker_id, NOW)
    assert task is not None

    submit_result(uow(engine), worker_id, task.task_id, OK, NOW + timedelta(seconds=5))

    assert query(
        engine, "SELECT status, price_minor, availability, title, confirmation, source, region_code FROM price_observations WHERE task_id = :t", t=task.task_id
    ) == [("ok", 100_000, "in_stock", "Лампа настольная", "confirmed", "site_data", "spb")]
    assert query(engine, "SELECT status, duration_ms, lease_until FROM check_tasks WHERE id = :t", t=task.task_id) == [("done", 1200, None)]
    assert query(engine, "SELECT r.status, r.done, r.failed FROM parse_runs r JOIN check_tasks t ON t.run_id = r.id WHERE t.id = :t", t=task.task_id) == [("finished", 1, 0)]
    assert query(engine, "SELECT baseline_title FROM tracked_links WHERE id = :l", l=added.tracked_link_id) == [("Лампа настольная",)]
    # Магазин узнал способ и своё название с сайта.
    assert query(engine, "SELECT fetch_method, display_name FROM stores WHERE id = :s", s=added.store_id) == [("http", "Магазин Ламп")]


def test_repeated_result_changes_nothing(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    add(engine)
    task = claim_task(uow(engine), worker_id, NOW)
    assert task is not None
    submit_result(uow(engine), worker_id, task.task_id, OK, NOW)

    submit_result(uow(engine), worker_id, task.task_id, OK, NOW + timedelta(seconds=30))

    assert query(engine, "SELECT count(*) FROM price_observations WHERE task_id = :t", t=task.task_id) == [(1,)]


def test_failure_result_is_recorded_without_price(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    add(engine)
    task = claim_task(uow(engine), worker_id, NOW)
    assert task is not None

    submit_result(uow(engine), worker_id, task.task_id, WorkerResult("LAYOUT_CHANGED", duration_ms=900), NOW)

    assert query(engine, "SELECT status, price_minor, confirmation FROM price_observations WHERE task_id = :t", t=task.task_id) == [("LAYOUT_CHANGED", None, "confirmed")]
    assert query(engine, "SELECT status, error_code FROM check_tasks WHERE id = :t", t=task.task_id) == [("failed", "LAYOUT_CHANGED")]
    assert query(engine, "SELECT r.failed FROM parse_runs r JOIN check_tasks t ON t.run_id = r.id WHERE t.id = :t", t=task.task_id) == [(1,)]


def test_result_for_someone_elses_or_expired_task_is_refused(engine: Engine) -> None:
    worker_id, stranger = worker(engine), worker(engine)
    drain(engine, worker_id)
    add(engine)
    task = claim_task(uow(engine), worker_id, NOW)
    assert task is not None

    for who, when in ((stranger, NOW), (worker_id, NOW + LEASE + timedelta(seconds=1))):
        with pytest.raises(PriceWatcherError) as caught:
            submit_result(uow(engine), who, task.task_id, OK, when)
        assert caught.value.code is ErrorCode.NOT_FOUND
    assert query(engine, "SELECT count(*) FROM price_observations WHERE task_id = :t", t=task.task_id) == [(0,)]


@pytest.mark.parametrize(
    ("result", "field"),
    [
        (WorkerResult("WHATEVER"), "status"),
        (WorkerResult("ok", None, None, "in_stock", source="site_data"), "price_minor"),  # в наличии без цены
        (WorkerResult("ok", 0, None, "in_stock", source="site_data"), "price_minor"),
        (WorkerResult("ok", 10**13, None, "in_stock", source="site_data"), "price_minor"),
        (WorkerResult("ok", 100, -5, "in_stock", source="site_data"), "card_price_minor"),
        (WorkerResult("ok", 100, None, "maybe", source="site_data"), "availability"),
        (WorkerResult("ok", 100, None, "in_stock"), "source"),
        (WorkerResult("ok", 100, None, "in_stock", source="guess"), "source"),
        (WorkerResult("ok", 100, None, "in_stock", "т" * 501, "site_data"), "title"),
        (WorkerResult("ok", 100, None, "in_stock", "Лампа\x00", "site_data"), "title"),
        (WorkerResult("ok", 100, None, "in_stock", source="site_data", fetch_method="ftp"), "fetch_method"),
        (WorkerResult("ok", 100, None, "in_stock", source="site_data", duration_ms=-1), "duration_ms"),
        (WorkerResult("TIMEOUT", 100), "price_minor"),  # сбой с ценой
    ],
)
def test_untrusted_result_is_validated(engine: Engine, result: WorkerResult, field: str) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    add(engine)
    task = claim_task(uow(engine), worker_id, NOW)
    assert task is not None

    with pytest.raises(PriceWatcherError) as caught:
        submit_result(uow(engine), worker_id, task.task_id, result, NOW)

    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert caught.value.details["fields"] == {field: "invalid"}


# --- подозрительные изменения ---


def check(engine: Engine, worker_id: int, link_host: str, path: str, result: WorkerResult, when: datetime) -> str:
    """Поставить ссылку в очередь заново, проверить и вернуть подтверждение наблюдения."""
    with engine.begin() as db:
        link_id = db.execute(text("SELECT id FROM tracked_links WHERE normalized_url = :u"), {"u": f"https://{link_host}/{path}"}).scalar_one()
    with uow(engine) as work:
        task_id = work.queue.enqueue_on_add(link_id, when)
        work.commit()
    task = claim_task(uow(engine), worker_id, when)
    assert task is not None and task.task_id == task_id
    submit_result(uow(engine), worker_id, task_id, result, when)
    return query(engine, "SELECT confirmation FROM price_observations WHERE task_id = :t", t=task_id)[0][0]


def test_price_jump_waits_and_closes_itself_when_price_returns(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    host = f"shop-{uuid.uuid4().hex[:8]}.example"
    added = add(engine, host, "lamp")
    task = claim_task(uow(engine), worker_id, NOW)
    submit_result(uow(engine), worker_id, task.task_id, OK, NOW)  # type: ignore[union-attr]

    jump = WorkerResult("ok", 40_000, None, "in_stock", "Лампа настольная", "site_data")
    assert check(engine, worker_id, host, "lamp", jump, NOW + timedelta(hours=1)) == "pending"
    back = WorkerResult("ok", 101_000, None, "in_stock", "Лампа настольная", "site_data")
    assert check(engine, worker_id, host, "lamp", back, NOW + timedelta(hours=2)) == "confirmed"

    rows = query(engine, "SELECT price_minor, confirmation FROM price_observations WHERE tracked_link_id = :l ORDER BY checked_at", l=added.tracked_link_id)
    assert rows == [(100_000, "confirmed"), (40_000, "rejected"), (101_000, "confirmed")]


def test_title_change_waits_for_admin_and_does_not_close_itself(engine: Engine) -> None:
    worker_id = worker(engine)
    drain(engine, worker_id)
    host = f"shop-{uuid.uuid4().hex[:8]}.example"
    added = add(engine, host, "lamp")
    task = claim_task(uow(engine), worker_id, NOW)
    submit_result(uow(engine), worker_id, task.task_id, OK, NOW)  # type: ignore[union-attr]

    other = WorkerResult("ok", 100_000, None, "in_stock", "Чехол для телефона", "site_data")
    assert check(engine, worker_id, host, "lamp", other, NOW + timedelta(hours=1)) == "pending"
    assert check(engine, worker_id, host, "lamp", OK, NOW + timedelta(hours=2)) == "confirmed"

    assert query(engine, "SELECT confirmation FROM price_observations WHERE tracked_link_id = :l ORDER BY checked_at", l=added.tracked_link_id) == [
        ("confirmed",), ("pending",), ("confirmed",)
    ]
