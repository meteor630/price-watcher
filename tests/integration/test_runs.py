"""Запуски на настоящей базе: какие ссылки входят, один запуск на всех, слот, остановка, просроченная аренда.

У модуля своя база: идущий плановый или ручной запуск один на всю базу, строки других модулей мешали бы.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta

import pytest
from concurrency_helpers import HeldWork, overlapped
from sqlalchemy import Engine, text

from pricewatch.application.runs import expire_leases, schedule_times, start_manual_run, start_scheduled_run, stop_run
from pricewatch.application.worker_protocol import LEASE, WorkerResult, claim_task, create_worker, submit_result
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
KEY = b"runs-key-" + b"k" * 32


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings CASCADE"))


def sql(engine: Engine, statement: str, **params: object) -> list[tuple]:
    with engine.begin() as db:
        result = db.execute(text(statement), params)
        return [tuple(row) for row in result] if result.returns_rows else []


def user(engine: Engine, role: str = "user", disabled: bool = False) -> int:
    return sql(
        engine,
        "INSERT INTO users (login, password_hash, role, display_name, disabled_at) "
        "VALUES ('u' || substr(md5(random()::text), 1, 10), 'h', :r, 'U', CASE WHEN :d THEN now() END) RETURNING id",
        r=role, d=disabled,
    )[0][0]


def store(engine: Engine, enabled: bool = True) -> int:
    return sql(
        engine,
        "INSERT INTO stores (canonical_host, display_name, color, parser_key, enabled, auto_created, min_delay_ms) "
        "VALUES (substr(md5(random()::text), 1, 8) || '.example', 'Магазин', 'store-1', 'universal', :e, true, 2000) RETURNING id",
        e=enabled,
    )[0][0]


def link(engine: Engine, store_id: int) -> int:
    return sql(
        engine,
        "INSERT INTO tracked_links (store_id, normalized_url, region_code) VALUES (:s, 'https://shop.example/p/' || md5(random()::text), 'spb') RETURNING id",
        s=store_id,
    )[0][0]


def item(engine: Engine, user_id: int, state: str = "active", purchased_at: datetime | None = None, deleted: bool = False) -> int:
    category = sql(
        engine, "INSERT INTO categories (user_id, name, name_normalized, position) SELECT :u, n, n, 0 FROM substr(md5(random()::text), 1, 8) n RETURNING id", u=user_id
    )[0][0]
    return sql(
        engine,
        "INSERT INTO items (user_id, category_id, name, position, state, purchased_at, deleted_at) "
        "VALUES (:u, :c, 'Лампа', 0, :s, :p, CASE WHEN :d THEN now() END) RETURNING id",
        u=user_id, c=category, s=state, p=purchased_at, d=deleted,
    )[0][0]


def bind(engine: Engine, user_id: int, item_id: int, link_id: int, closed: bool = False) -> None:
    sql(
        engine,
        "INSERT INTO item_links (user_id, item_id, tracked_link_id, store_id, valid_from, valid_to) "
        "SELECT :u, :i, id, store_id, :f, CASE WHEN :c THEN :t END FROM tracked_links WHERE id = :l",
        u=user_id, i=item_id, l=link_id, f=NOW - timedelta(days=30), c=closed, t=NOW - timedelta(days=1),
    )


def watched_link(engine: Engine, user_id: int | None = None, store_id: int | None = None) -> int:
    """Ссылка, которую отслеживает товар «в списке»."""
    owner = user_id or user(engine)
    link_id = link(engine, store_id or store(engine))
    bind(engine, owner, item(engine, owner), link_id)
    return link_id


def run_links(engine: Engine, run_id: int) -> set[int]:
    return {row[0] for row in sql(engine, "SELECT tracked_link_id FROM check_tasks WHERE run_id = :r", r=run_id)}


def run_row(engine: Engine, run_id: int) -> tuple:
    return sql(engine, "SELECT status, total, done, failed, finished_at IS NOT NULL FROM parse_runs WHERE id = :r", r=run_id)[0]


def worker_id(engine: Engine) -> int:
    create_worker(SqlUnitOfWork(engine), KEY, "w", NOW)
    return sql(engine, "SELECT id FROM workers WHERE name = 'w'")[0][0]


# --- какие ссылки входят ---


def test_run_takes_links_that_need_checking(engine: Engine) -> None:
    admin, owner, blocked = user(engine, "admin"), user(engine), user(engine, disabled=True)
    shop, off = store(engine), store(engine, enabled=False)
    expected = {
        "в списке": watched_link(engine, owner, shop),
        "куплен 13 суток назад": link(engine, shop),
    }
    bind(engine, owner, item(engine, owner, "purchased", NOW - timedelta(days=13)), expected["куплен 13 суток назад"])
    skipped = {name: link(engine, shop) for name in ("куплен ровно 14 суток", "архив", "удалён", "привязка закрыта", "пользователь заблокирован")}
    bind(engine, owner, item(engine, owner, "purchased", NOW - timedelta(days=14)), skipped["куплен ровно 14 суток"])
    bind(engine, owner, item(engine, owner, "archived"), skipped["архив"])
    bind(engine, owner, item(engine, owner, deleted=True), skipped["удалён"])
    bind(engine, owner, item(engine, owner), skipped["привязка закрыта"], closed=True)
    bind(engine, blocked, item(engine, blocked), skipped["пользователь заблокирован"])
    skipped["магазин выключен"] = watched_link(engine, owner, off)

    progress = start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    assert run_links(engine, progress.run_id) == set(expected.values())
    assert (progress.kind, progress.status, progress.total, progress.done) == ("manual", "queued", 2, 0)


def test_shared_link_is_one_task(engine: Engine) -> None:
    admin, first, second = user(engine, "admin"), user(engine), user(engine)
    shared = watched_link(engine, first)
    bind(engine, second, item(engine, second), shared)

    progress = start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    assert progress.total == 1 and run_links(engine, progress.run_id) == {shared}


def test_link_already_being_checked_is_not_queued_twice(engine: Engine) -> None:
    admin = user(engine, "admin")
    busy, free = watched_link(engine), watched_link(engine)
    on_add = SqlUnitOfWork(engine)
    with on_add:
        on_add.queue.enqueue_on_add(busy, NOW)
        on_add.commit()

    progress = start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    assert run_links(engine, progress.run_id) == {free} and progress.total == 1


def test_nothing_to_check_finishes_at_once(engine: Engine) -> None:
    progress = start_manual_run(SqlUnitOfWork(engine), user(engine, "admin"), NOW)

    assert (progress.status, progress.total) == ("finished", 0)
    assert run_row(engine, progress.run_id)[4] is True


# --- один запуск на всех ---


def test_second_manual_run_gets_progress_of_running_one(engine: Engine) -> None:
    admin = user(engine, "admin")
    watched_link(engine)
    first = start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    with pytest.raises(PriceWatcherError) as caught:
        start_manual_run(SqlUnitOfWork(engine), user(engine, "admin"), NOW)

    assert caught.value.code is ErrorCode.RUN_IN_PROGRESS
    assert caught.value.details["run"] == {"run_id": first.run_id, "kind": "manual", "total": 1, "done": 0, "failed": 0}


def test_two_admins_press_start_at_the_same_time(engine: Engine) -> None:
    first_admin, second_admin = user(engine, "admin"), user(engine, "admin")
    watched_link(engine)

    first, second = overlapped(
        engine,
        lambda work: start_manual_run(HeldWork(work), first_admin, NOW),  # type: ignore[arg-type]
        lambda work: start_manual_run(HeldWork(work), second_admin, NOW),  # type: ignore[arg-type]
    )

    assert first.error is None and second.error is ErrorCode.RUN_IN_PROGRESS
    assert sql(engine, "SELECT count(*) FROM parse_runs WHERE kind = 'manual'") == [(1,)]


def test_manual_over_scheduled_and_scheduled_over_manual(engine: Engine) -> None:
    admin = user(engine, "admin")
    watched_link(engine)
    scheduled = start_scheduled_run(SqlUnitOfWork(engine), "2026-10-06T09:00", NOW)
    assert scheduled.progress is not None

    with pytest.raises(PriceWatcherError) as caught:
        start_manual_run(SqlUnitOfWork(engine), admin, NOW)
    assert caught.value.code is ErrorCode.RUN_IN_PROGRESS and caught.value.details["run"]["kind"] == "scheduled"

    stop_run(SqlUnitOfWork(engine), admin, scheduled.progress.run_id, NOW)
    manual = start_manual_run(SqlUnitOfWork(engine), admin, NOW)
    blocked = start_scheduled_run(SqlUnitOfWork(engine), "2026-10-06T14:00", NOW)

    assert blocked.progress is None and blocked.blocked_by is not None and blocked.blocked_by.run_id == manual.run_id
    assert sql(engine, "SELECT count(*) FROM parse_runs WHERE slot_key = '2026-10-06T14:00'") == [(0,)]


def test_on_add_task_does_not_block_runs(engine: Engine) -> None:
    link_id = watched_link(engine)
    on_add = SqlUnitOfWork(engine)
    with on_add:
        on_add.queue.enqueue_on_add(link_id, NOW)
        on_add.commit()

    assert start_scheduled_run(SqlUnitOfWork(engine), "2026-10-06T09:00", NOW).progress is not None


# --- слот планового запуска ---


def test_slot_runs_once(engine: Engine) -> None:
    watched_link(engine)
    first = start_scheduled_run(SqlUnitOfWork(engine), "2026-10-06T09:00", NOW)
    assert first.progress is not None
    stop_run(SqlUnitOfWork(engine), user(engine, "admin"), first.progress.run_id, NOW)

    again = start_scheduled_run(SqlUnitOfWork(engine), "2026-10-06T09:00", NOW)

    assert again.slot_done is True and again.progress is None


def test_two_schedulers_same_slot(engine: Engine) -> None:
    watched_link(engine)

    first, second = overlapped(
        engine,
        lambda work: start_scheduled_run(HeldWork(work), "2026-10-06T09:00", NOW),  # type: ignore[arg-type]
        lambda work: start_scheduled_run(HeldWork(work), "2026-10-06T09:00", NOW),  # type: ignore[arg-type]
    )

    assert first.value.progress is not None  # type: ignore[union-attr]
    assert second.value.slot_done is True  # type: ignore[union-attr]
    assert sql(engine, "SELECT count(*) FROM parse_runs WHERE slot_key = '2026-10-06T09:00'") == [(1,)]


def test_schedule_times_default_and_setting(engine: Engine) -> None:
    assert schedule_times(SqlUnitOfWork(engine)) == (time(9, 0),)

    sql(engine, "INSERT INTO settings (key, value_json) VALUES ('check_times', '[\"21:00\", \"09:30\"]')")

    assert schedule_times(SqlUnitOfWork(engine)) == (time(9, 30), time(21, 0))


def test_broken_schedule_setting_is_an_error_not_a_default(engine: Engine) -> None:
    sql(engine, "INSERT INTO settings (key, value_json) VALUES ('check_times', '[\"9 утра\"]')")

    with pytest.raises(ValueError):
        schedule_times(SqlUnitOfWork(engine))


# --- остановка ---


def test_stop_cancels_waiting_and_lets_leased_finish(engine: Engine) -> None:
    admin, worker = user(engine, "admin"), worker_id(engine)
    for _ in range(3):
        watched_link(engine)
    run = start_manual_run(SqlUnitOfWork(engine), admin, NOW)
    leased = claim_task(SqlUnitOfWork(engine), worker, NOW)
    assert leased is not None

    stopping = stop_run(SqlUnitOfWork(engine), admin, run.run_id, NOW)
    assert stopping.status == "stopping"
    assert claim_task(SqlUnitOfWork(engine), worker, NOW) is None  # новые задания не выдаются

    submit_result(SqlUnitOfWork(engine), worker, leased.task_id, WorkerResult("TIMEOUT"), NOW)

    assert run_row(engine, run.run_id) == ("finished", 3, 0, 1, True)
    assert sql(engine, "SELECT status, count(*) FROM check_tasks WHERE run_id = :r GROUP BY status ORDER BY status", r=run.run_id) == [
        ("cancelled", 2), ("failed", 1),
    ]


def test_stop_without_leased_tasks_finishes_at_once(engine: Engine) -> None:
    admin = user(engine, "admin")
    watched_link(engine)
    run = start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    assert stop_run(SqlUnitOfWork(engine), admin, run.run_id, NOW).status == "finished"


def test_stop_finished_or_unknown_run_is_not_found(engine: Engine) -> None:
    admin = user(engine, "admin")
    finished = start_manual_run(SqlUnitOfWork(engine), admin, NOW)  # пустой — завершён сразу

    for run_id in (finished.run_id, 999_999):
        with pytest.raises(PriceWatcherError) as caught:
            stop_run(SqlUnitOfWork(engine), admin, run_id, NOW)
        assert caught.value.code is ErrorCode.NOT_FOUND


def test_on_add_run_cannot_be_stopped(engine: Engine) -> None:
    link_id = watched_link(engine)
    on_add = SqlUnitOfWork(engine)
    with on_add:
        on_add.queue.enqueue_on_add(link_id, NOW)
        on_add.commit()
    run_id = sql(engine, "SELECT id FROM parse_runs WHERE kind = 'on_add'")[0][0]

    with pytest.raises(PriceWatcherError) as caught:
        stop_run(SqlUnitOfWork(engine), user(engine, "admin"), run_id, NOW)

    assert caught.value.code is ErrorCode.NOT_FOUND


# --- просроченная аренда ---


def test_expired_lease_returns_task_to_queue(engine: Engine) -> None:
    worker = worker_id(engine)
    watched_link(engine)
    run = start_manual_run(SqlUnitOfWork(engine), user(engine, "admin"), NOW)
    task = claim_task(SqlUnitOfWork(engine), worker, NOW)
    assert task is not None
    later = NOW + LEASE + timedelta(seconds=1)

    assert expire_leases(SqlUnitOfWork(engine), NOW + LEASE).requeued == 0  # ровно в срок аренда ещё действует
    expired = expire_leases(SqlUnitOfWork(engine), later)

    assert (expired.requeued, expired.failed) == (1, 0)
    with pytest.raises(PriceWatcherError) as caught:  # опоздавший результат прежнего держателя не принимается
        submit_result(SqlUnitOfWork(engine), worker, task.task_id, WorkerResult("TIMEOUT"), later)
    assert caught.value.code is ErrorCode.NOT_FOUND
    again = claim_task(SqlUnitOfWork(engine), worker, later)
    assert again is not None and again.task_id == task.task_id
    assert sql(engine, "SELECT attempt FROM check_tasks WHERE id = :t", t=task.task_id) == [(2,)]
    assert run_row(engine, run.run_id)[0] == "running"


def test_third_expired_lease_fails_task_into_history(engine: Engine) -> None:
    worker = worker_id(engine)
    link_id = watched_link(engine)
    run = start_manual_run(SqlUnitOfWork(engine), user(engine, "admin"), NOW)
    moment = NOW
    for _ in range(3):
        assert claim_task(SqlUnitOfWork(engine), worker, moment) is not None
        moment += LEASE + timedelta(seconds=1)
        expired = expire_leases(SqlUnitOfWork(engine), moment)

    assert (expired.requeued, expired.failed) == (0, 1)
    assert sql(engine, "SELECT status, error_code FROM check_tasks WHERE run_id = :r", r=run.run_id) == [("failed", "TIMEOUT")]
    assert sql(engine, "SELECT status, availability, price_minor FROM price_observations WHERE tracked_link_id = :l", l=link_id) == [
        ("TIMEOUT", "unknown", None)
    ]
    assert run_row(engine, run.run_id) == ("finished", 1, 0, 1, True)


def test_expired_task_of_stopped_run_is_cancelled(engine: Engine) -> None:
    admin, worker = user(engine, "admin"), worker_id(engine)
    watched_link(engine)
    run = start_manual_run(SqlUnitOfWork(engine), admin, NOW)
    assert claim_task(SqlUnitOfWork(engine), worker, NOW) is not None
    stop_run(SqlUnitOfWork(engine), admin, run.run_id, NOW)

    expired = expire_leases(SqlUnitOfWork(engine), NOW + LEASE + timedelta(seconds=1))

    assert expired.cancelled == 1
    assert run_row(engine, run.run_id)[0] == "finished"


def test_lease_being_answered_is_skipped(engine: Engine) -> None:
    worker = worker_id(engine)
    watched_link(engine)
    start_manual_run(SqlUnitOfWork(engine), user(engine, "admin"), NOW)
    task = claim_task(SqlUnitOfWork(engine), worker, NOW)
    assert task is not None

    with SqlUnitOfWork(engine) as answering:
        assert answering.queue.leased(worker, task.task_id, NOW + LEASE) is not None  # результат принимается и держит задание
        expired = expire_leases(SqlUnitOfWork(engine), NOW + LEASE + timedelta(seconds=1))

    assert expired.requeued == 0


def test_last_result_finishes_run(engine: Engine) -> None:
    worker = worker_id(engine)
    watched_link(engine)
    run = start_manual_run(SqlUnitOfWork(engine), user(engine, "admin"), NOW)
    task = claim_task(SqlUnitOfWork(engine), worker, NOW)
    assert task is not None

    submit_result(SqlUnitOfWork(engine), worker, task.task_id, WorkerResult("ok", 129_900, availability="in_stock", source="site_data"), NOW)

    assert run_row(engine, run.run_id) == ("finished", 1, 1, 0, True)


# --- только администратор ---


@pytest.mark.parametrize(("role", "disabled"), [("user", False), ("admin", True)])
def test_only_active_admin_starts_and_stops(engine: Engine, role: str, disabled: bool) -> None:
    watched_link(engine)
    run = start_scheduled_run(SqlUnitOfWork(engine), "2026-10-06T09:00", NOW).progress
    assert run is not None
    actor = user(engine, role, disabled)

    for action in (lambda: start_manual_run(SqlUnitOfWork(engine), actor, NOW), lambda: stop_run(SqlUnitOfWork(engine), actor, run.run_id, NOW)):
        with pytest.raises(PriceWatcherError) as caught:
            action()
        assert caught.value.code is ErrorCode.FORBIDDEN
    assert run_row(engine, run.run_id)[0] == "queued"
