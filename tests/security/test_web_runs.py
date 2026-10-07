"""Проверка цен на сайте: блок запуска только у администратора, запуск и остановка, второй запуск, опрос статуса, CSRF."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_web_home import NOW, account_with_list, client, engine, no_inline_code, sign_in, sql  # noqa: F401 — фикстуры модуля
from test_web_settings_items import htmx

POLL = 'hx-trigger="every 5s"'


def no_active_runs(engine: Engine) -> None:
    sql(engine, "WITH t AS (UPDATE check_tasks SET status = 'cancelled', finished_at = :t WHERE status IN ('queued', 'leased') "
                "AND run_id IN (SELECT id FROM parse_runs WHERE kind IN ('manual', 'scheduled')) RETURNING 1) SELECT count(*) FROM t", t=NOW)
    sql(engine, "WITH r AS (UPDATE parse_runs SET status = 'finished', finished_at = :t WHERE status IN ('queued', 'running', 'stopping') "
                "AND kind IN ('manual', 'scheduled') RETURNING 1) SELECT count(*) FROM r", t=NOW)


@pytest.fixture(autouse=True)
def quiet_queue(engine: Engine) -> Iterator[None]:
    # База общая на весь прогон: запуск одного теста не должен остаться «идущим» для других.
    no_active_runs(engine)
    sql(engine, "INSERT INTO workers (name, token_hash, last_seen_at) VALUES (:n, md5(:n) || md5(:n), :t) RETURNING id", n=uuid.uuid4().hex, t=NOW)
    yield
    no_active_runs(engine)


def admin(engine: Engine) -> str:
    login = account_with_list(engine, items=1)
    sql(engine, "UPDATE users SET role = 'admin' WHERE login = :l RETURNING id", l=login)
    return login


def test_home_shows_run_panel_only_to_admin(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=1))
    assert "data-run-panel" not in client.get("/web/").text
    assert client.get("/web/runs/panel").status_code == 403

    sign_in(client, admin(engine))
    home = client.get("/web/").text

    assert "data-run-panel" in home and "Запустить проверку" in home
    no_inline_code(home)


def test_start_polls_and_second_start_shows_progress(client: TestClient, engine: Engine) -> None:
    sign_in(client, admin(engine))

    started = client.post("/web/runs", headers=htmx(client))
    again = client.post("/web/runs", headers=htmx(client))

    assert started.status_code == 200
    assert "Идёт ручная проверка: 0 из" in started.text and POLL in started.text and "Остановить" in started.text
    assert 'id="run-status"' in started.text and 'hx-swap-oob="true"' in started.text  # статус шапки обновляется вместе с блоком
    assert "Запустить проверку" not in started.text
    no_inline_code(started.text)
    assert again.status_code == 200 and 'data-error-code="RUN_IN_PROGRESS"' in again.text and "Идёт ручная проверка" in again.text
    status = client.get("/web/runs/status").text
    assert POLL in status and "Идёт проверка" in status


def test_stop_ends_polling(client: TestClient, engine: Engine) -> None:
    sign_in(client, admin(engine))
    client.post("/web/runs", headers=htmx(client))
    run_id = sql(engine, "SELECT id FROM parse_runs WHERE kind = 'manual' AND status = 'queued'")

    stopped = client.post(f"/web/runs/{run_id}/stop", headers=htmx(client))

    assert stopped.status_code == 200 and POLL not in stopped.text and "Последняя проверка" in stopped.text
    assert POLL not in client.get("/web/runs/status").text
    assert client.post(f"/web/runs/{run_id}/stop", headers=htmx(client)).status_code == 404


def test_user_cannot_start_and_csrf_is_required(client: TestClient, engine: Engine) -> None:
    sign_in(client, admin(engine))
    assert client.post("/web/runs", headers={"HX-Request": "true"}).status_code == 403  # без CSRF
    sign_in(client, account_with_list(engine, items=0))

    refused = client.post("/web/runs", headers=htmx(client))

    assert refused.status_code == 403 and 'data-error-code="FORBIDDEN"' in refused.text
    assert sql(engine, "SELECT count(*) FROM parse_runs WHERE kind = 'manual' AND status = 'queued'") == 0


def test_status_needs_session(client: TestClient) -> None:
    response = client.get("/web/runs/status", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/web/login"
