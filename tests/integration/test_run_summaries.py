"""Сводка проверки на настоящей базе: «изменения есть / нет» каждому с привязанным Telegram, изменения — по ценам,
засчитанным самому пользователю; одна сводка на запуск даже при двух планировщиках; on_add и идущий запуск — без сводки;
после обновления схемы старые запуски сводок не рассылают."""

import threading
from collections.abc import Iterator
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from test_dashboard import DAY, NOW, World
from test_user_decisions import decide, suspicious, watch

from pricewatch.application.summaries import send_run_summaries
from pricewatch.application.telegram import claim_messages
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.schema import alembic_config
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

RUN_START, RUN_END = NOW - timedelta(hours=1), NOW - timedelta(minutes=10)


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings, notifications, parse_runs CASCADE"))


def linked(world: World, chat: int | None = None) -> World:
    world.one("UPDATE users SET telegram_chat_id = :c WHERE id = :u RETURNING id", c=chat or 700_000_000 + world.user.id, u=world.user.id)
    return world


def finished_run(world: World, kind: str = "scheduled", status: str = "finished") -> int:
    return world.one(
        "INSERT INTO parse_runs (kind, slot_key, requested_by, status, created_at, finished_at) "
        "VALUES (:k, CASE WHEN :k = 'scheduled' THEN 'slot-' || md5(random()::text) END, CASE WHEN :k = 'manual' THEN :u END, :s, :c, "
        "CASE WHEN :s = 'finished' THEN CAST(:f AS timestamptz) END) RETURNING id",
        k=kind, u=world.user.id, s=status, c=RUN_START, f=RUN_END,
    )


def summaries(engine: Engine) -> dict[int, bool]:
    with engine.connect() as db:
        rows = db.execute(text("SELECT user_id, (payload_json->>'changed')::boolean FROM notifications WHERE kind = 'run_summary' AND channel = 'telegram'"))
        return {row[0]: row[1] for row in rows}


def summarize(engine: Engine) -> int:
    return send_run_summaries(SqlUnitOfWork(engine), NOW)


def priced_item(world: World, state: str = "active") -> int:
    return world.link(world.item(world.category(f"Кат {state}"), "Лампа", state=state), world.store())


def test_changed_and_unchanged_users_get_one_summary_each(engine: Engine) -> None:
    moved, steady, silent = linked(World(engine)), linked(World(engine)), World(engine)
    blocked = linked(World(engine))
    blocked.one("UPDATE users SET disabled_at = :t WHERE id = :u RETURNING id", t=NOW - DAY, u=blocked.user.id)
    for world, new_price in ((moved, 90_000), (steady, 100_000), (silent, 90_000), (blocked, 90_000)):
        link = priced_item(world)
        world.seen(link, NOW - 2 * DAY, 100_000)
        world.seen(link, NOW - timedelta(minutes=30), new_price)
    run = finished_run(moved)

    assert summarize(engine) == 1
    assert summarize(engine) == 0

    assert summaries(engine) == {moved.user.id: True, steady.user.id: False}
    assert moved.one("SELECT summarized_at FROM parse_runs WHERE id = :r", r=run) == NOW
    texts = {message.chat_id: message.text for message in claim_messages(SqlUnitOfWork(engine), NOW, 50)}
    assert texts == {700_000_000 + moved.user.id: "Проверка завершена: изменения есть.", 700_000_000 + steady.user.id: "Проверка завершена: изменений нет."}


def test_user_without_items_gets_no_changes(engine: Engine) -> None:
    lonely = linked(World(engine))
    finished_run(lonely, kind="manual")

    summarize(engine)

    assert summaries(engine) == {lonely.user.id: False}


@pytest.mark.parametrize(
    ("before", "during", "changed"),
    [
        ({"price": 100_000}, {"availability": "out_of_stock"}, True),
        ({"availability": "out_of_stock"}, {"price": 100_000}, True),
        ({"price": 100_000, "card": 90_000}, {"price": 120_000, "card": 90_000}, False),  # действующая — по карте, не изменилась
        ({"price": 100_000}, {"status": "TIMEOUT"}, False),
    ],
)
def test_what_counts_as_change(engine: Engine, before: dict, during: dict, changed: bool) -> None:
    world = linked(World(engine))
    link = priced_item(world)
    world.seen(link, NOW - 2 * DAY, **before)
    world.seen(link, NOW - timedelta(minutes=30), **during)
    finished_run(world)

    summarize(engine)

    assert summaries(engine) == {world.user.id: changed}


def test_change_before_the_run_and_archived_items_do_not_count(engine: Engine) -> None:
    world = linked(World(engine))
    earlier = priced_item(world)
    world.seen(earlier, NOW - 2 * DAY, 100_000)
    world.seen(earlier, NOW - DAY, 80_000)
    world.seen(earlier, NOW - timedelta(minutes=30), 80_000)
    archived = priced_item(world, state="archived")
    world.seen(archived, NOW - 2 * DAY, 100_000)
    world.seen(archived, NOW - timedelta(minutes=30), 50_000)
    finished_run(world)

    summarize(engine)

    assert summaries(engine) == {world.user.id: False}


def test_purchased_item_still_checked_counts(engine: Engine) -> None:
    world = linked(World(engine))
    link = priced_item(world, state="purchased")
    world.seen(link, NOW - 2 * DAY, 100_000)
    world.seen(link, NOW - timedelta(minutes=30), 95_000)
    finished_run(world)

    summarize(engine)

    assert summaries(engine) == {world.user.id: True}


def test_suspicious_change_counts_only_for_who_accepted(engine: Engine) -> None:
    accepted, waiting = linked(World(engine)), linked(World(engine))
    store = accepted.store("Ozon")
    link = accepted.link(accepted.item(accepted.category("Компьютер"), "Монитор"), store)
    watch(waiting, link, store)
    accepted.seen(link, NOW - 2 * DAY, 30_000_00)
    jump = suspicious(accepted, link, NOW - timedelta(minutes=30), 45_000_00)
    decide(accepted, jump, "accept")
    finished_run(accepted)

    summarize(engine)

    assert summaries(engine) == {accepted.user.id: True, waiting.user.id: False}


@pytest.mark.parametrize(("kind", "status"), [("on_add", "finished"), ("scheduled", "running"), ("manual", "queued")])
def test_on_add_and_unfinished_runs_have_no_summary(engine: Engine, kind: str, status: str) -> None:
    world = linked(World(engine))
    finished_run(world, kind=kind, status=status)

    assert summarize(engine) == 0
    assert summaries(engine) == {}


def test_two_schedulers_make_one_summary(engine: Engine) -> None:
    world = linked(World(engine))
    finished_run(world)
    counts: list[int] = []
    start = threading.Barrier(2)

    def scheduler() -> None:
        start.wait()
        counts.append(summarize(engine))

    threads = [threading.Thread(target=scheduler) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(counts) == [0, 1]
    assert world.one("SELECT count(*) FROM notifications WHERE kind = 'run_summary'") == 1


def test_upgrade_marks_old_runs_summarized(empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)
    command.upgrade(config, "0013")
    db_engine = create_engine(empty_database_url)
    try:
        with db_engine.begin() as db:
            db.execute(text(
                "INSERT INTO parse_runs (kind, slot_key, status, created_at, finished_at) VALUES "
                "('scheduled', 'old', 'finished', :c, :f), ('scheduled', 'now', 'running', :c, NULL)"
            ), {"c": RUN_START, "f": RUN_END})
        command.upgrade(config, "head")
        with db_engine.connect() as db:
            rows = {row.slot_key: row.summarized_at for row in db.execute(text("SELECT slot_key, summarized_at FROM parse_runs"))}
    finally:
        db_engine.dispose()

    assert rows == {"old": RUN_END, "now": None}
