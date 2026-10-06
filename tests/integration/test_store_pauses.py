"""Паузы магазина на настоящей базе: ступени 403/429 и капчи, задания ждут, исчерпание, уведомления, сброс.

У модуля своя база: проверки опираются на идущий запуск, а он один на всю базу.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from test_runs import run_row, sql, store, user, watched_link, worker_id

from pricewatch.application.ports import ClaimedTask
from pricewatch.application.runs import start_manual_run
from pricewatch.application.worker_protocol import WorkerResult, claim_task, submit_result
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # 15:00 по Москве
NEXT_NINE_MOSCOW = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings CASCADE"))


def claim(engine: Engine, worker: int, at: datetime) -> ClaimedTask | None:
    return claim_task(SqlUnitOfWork(engine), worker, at)


def answer(engine: Engine, worker: int, task: ClaimedTask, status: str, at: datetime) -> None:
    result = WorkerResult("ok", 1000, availability="in_stock", source="site_data") if status == "ok" else WorkerResult(status)
    submit_result(SqlUnitOfWork(engine), worker, task.task_id, result, at)


def pause_of(engine: Engine, store_id: int) -> tuple:
    return sql(engine, "SELECT pause_reason, pause_level, paused_until FROM stores WHERE id = :s", s=store_id)[0]


def task_state(engine: Engine, task_id: int) -> tuple:
    return sql(engine, "SELECT status, attempt, error_code FROM check_tasks WHERE id = :t", t=task_id)[0]


def history(engine: Engine, link_id: int) -> list[str]:
    return [row[0] for row in sql(engine, "SELECT status FROM price_observations WHERE tracked_link_id = :l ORDER BY id", l=link_id)]


def test_blocked_ladder_then_rest_until_next_run(engine: Engine) -> None:
    admin, worker, shop = user(engine, "admin"), worker_id(engine), store(engine)
    first, second = watched_link(engine, store_id=shop), watched_link(engine, store_id=shop)
    run = start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    task = claim(engine, worker, NOW)
    assert task is not None
    answer(engine, worker, task, "BLOCKED", NOW)
    assert pause_of(engine, shop) == ("BLOCKED", 1, NOW + timedelta(minutes=15))
    assert task_state(engine, task.task_id) == ("queued", 0, None)  # ждёт, попытка не потрачена
    assert claim(engine, worker, NOW + timedelta(minutes=14)) is None  # магазин отдыхает

    moment = NOW + timedelta(minutes=15)
    task = claim(engine, worker, moment)
    assert task is not None
    answer(engine, worker, task, "BLOCKED", moment)
    assert pause_of(engine, shop) == ("BLOCKED", 2, moment + timedelta(hours=1))

    moment += timedelta(hours=1)
    task = claim(engine, worker, moment)
    assert task is not None
    answer(engine, worker, task, "BLOCKED", moment)

    assert pause_of(engine, shop) == ("BLOCKED", 3, NEXT_NINE_MOSCOW)
    assert history(engine, task.tracked_link_id) == ["BLOCKED"]  # проверенная ссылка — в истории
    assert history(engine, ({first, second} - {task.tracked_link_id}).pop()) == []  # непроверенная — нет
    assert sql(engine, "SELECT status, error_code FROM check_tasks WHERE run_id = :r", r=run.run_id) == [("failed", "BLOCKED")] * 2
    assert run_row(engine, run.run_id) == ("finished", 2, 0, 2, True)


def test_captcha_pauses_are_shorter(engine: Engine) -> None:
    admin, worker, shop = user(engine, "admin"), worker_id(engine), store(engine)
    watched_link(engine, store_id=shop)
    start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    task = claim(engine, worker, NOW)
    assert task is not None
    answer(engine, worker, task, "CAPTCHA", NOW)
    assert pause_of(engine, shop) == ("CAPTCHA", 1, NOW + timedelta(seconds=15))

    task = claim(engine, worker, NOW + timedelta(seconds=15))
    assert task is not None
    answer(engine, worker, task, "CAPTCHA", NOW + timedelta(seconds=15))
    assert pause_of(engine, shop) == ("CAPTCHA", 2, NOW + timedelta(seconds=75))


def test_exhausted_pause_notifies_active_admins(engine: Engine) -> None:
    worker, shop = worker_id(engine), store(engine)
    with_telegram, without_telegram = user(engine, "admin"), user(engine, "admin")
    user(engine, "admin", disabled=True)
    user(engine)
    sql(engine, "UPDATE users SET telegram_chat_id = 4242 WHERE id = :u", u=with_telegram)
    sql(engine, "UPDATE stores SET pause_reason = 'CAPTCHA', pause_level = 2, paused_until = :p WHERE id = :s", p=NOW, s=shop)
    watched_link(engine, store_id=shop)
    start_manual_run(SqlUnitOfWork(engine), with_telegram, NOW)

    task = claim(engine, worker, NOW)
    assert task is not None
    answer(engine, worker, task, "CAPTCHA", NOW)

    rows = sql(engine, "SELECT user_id, channel, kind, payload_json FROM notifications ORDER BY user_id, channel")
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (with_telegram, "in_app", "store_paused"), (with_telegram, "telegram", "store_paused"), (without_telegram, "in_app", "store_paused"),
    ]
    payload = rows[0][3]
    assert (payload["reason"], payload["paused_until"]) == ("CAPTCHA", NEXT_NINE_MOSCOW.isoformat())
    assert payload["host"].endswith(".example")


def test_same_notification_is_not_repeated(engine: Engine) -> None:
    admin = user(engine, "admin")
    work = SqlUnitOfWork(engine)
    with work:
        created = [work.notifications.notify_admins("store_paused", "store_paused:1:x", {"a": 1}, NOW) for _ in range(2)]
        work.commit()

    assert created == [1, 0]
    assert sql(engine, "SELECT count(*) FROM notifications WHERE user_id = :u", u=admin) == [(1,)]


def test_rest_lasts_until_next_slot_from_settings(engine: Engine) -> None:
    admin, worker, shop = user(engine, "admin"), worker_id(engine), store(engine)
    sql(engine, "INSERT INTO settings (key, value_json) VALUES ('check_times', '[\"09:00\", \"18:30\"]')")
    sql(engine, "UPDATE stores SET pause_reason = 'BLOCKED', pause_level = 2, paused_until = :p WHERE id = :s", p=NOW, s=shop)
    watched_link(engine, store_id=shop)
    start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    task = claim(engine, worker, NOW)
    assert task is not None
    answer(engine, worker, task, "BLOCKED", NOW)

    assert pause_of(engine, shop)[2] == datetime(2026, 10, 6, 15, 30, tzinfo=UTC)  # 18:30 по Москве сегодня


def test_any_other_answer_clears_pause(engine: Engine) -> None:
    admin, worker, shop = user(engine, "admin"), worker_id(engine), store(engine)
    sql(engine, "UPDATE stores SET pause_reason = 'BLOCKED', pause_level = 2, paused_until = :p WHERE id = :s", p=NOW, s=shop)
    watched_link(engine, store_id=shop)
    start_manual_run(SqlUnitOfWork(engine), admin, NOW)

    task = claim(engine, worker, NOW)
    assert task is not None
    answer(engine, worker, task, "PRICE_NOT_FOUND", NOW)

    assert pause_of(engine, shop) == (None, 0, None)


def test_paused_store_does_not_hold_up_others(engine: Engine) -> None:
    admin, worker = user(engine, "admin"), worker_id(engine)
    paused, other = store(engine), store(engine)
    watched_link(engine, store_id=paused)
    other_link = watched_link(engine, store_id=other)
    start_manual_run(SqlUnitOfWork(engine), admin, NOW)
    sql(engine, "UPDATE check_tasks SET priority = 1 WHERE tracked_link_id <> :l", l=other_link)  # сначала выдаётся задание будущего «на паузе»

    task = claim(engine, worker, NOW)
    assert task is not None and task.tracked_link_id != other_link
    answer(engine, worker, task, "CAPTCHA", NOW)

    following = claim(engine, worker, NOW)
    assert following is not None and following.tracked_link_id == other_link


def test_pauses_do_not_use_up_lease_attempts(engine: Engine) -> None:
    admin, worker, shop = user(engine, "admin"), worker_id(engine), store(engine)
    watched_link(engine, store_id=shop)
    start_manual_run(SqlUnitOfWork(engine), admin, NOW)
    moment = NOW
    for _ in range(2):
        task = claim(engine, worker, moment)
        assert task is not None
        answer(engine, worker, task, "CAPTCHA", moment)
        moment += timedelta(minutes=2)

    task = claim(engine, worker, moment)
    assert task is not None
    assert task_state(engine, task.task_id) == ("leased", 1, None)
