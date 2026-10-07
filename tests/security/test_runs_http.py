"""/api/v1/runs: состояние по схеме, запуск и остановка только администратором, ключ повтора, второй запуск — 409 с прогрессом."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_dashboard_http import NOW, bearer, client, engine, run_sql, schema, user_with_list  # noqa: F401 — фикстуры модуля

RUN_SCHEMA = schema("RunStatus")
ERROR_SCHEMA = schema("ErrorResponse")


def no_active_runs(engine: Engine) -> None:
    run_sql(engine, "WITH t AS (UPDATE check_tasks SET status = 'cancelled', finished_at = :t WHERE status IN ('queued', 'leased') "
                    "AND run_id IN (SELECT id FROM parse_runs WHERE kind IN ('manual', 'scheduled')) RETURNING 1) SELECT count(*) FROM t", t=NOW)
    run_sql(engine, "WITH r AS (UPDATE parse_runs SET status = 'finished', finished_at = :t WHERE status IN ('queued', 'running', 'stopping') "
                    "AND kind IN ('manual', 'scheduled') RETURNING 1) SELECT count(*) FROM r", t=NOW)


@pytest.fixture(autouse=True)
def quiet_queue(engine: Engine) -> Iterator[None]:
    # База общая на весь прогон: запуск одного теста не должен остаться «идущим» для других.
    no_active_runs(engine)
    run_sql(engine, "INSERT INTO workers (name, token_hash, last_seen_at) VALUES (:n, md5(:n) || md5(:n), :t) RETURNING id", n=uuid.uuid4().hex, t=NOW)
    yield
    no_active_runs(engine)


def admin(engine: Engine) -> int:
    user = user_with_list(engine, items=1)
    run_sql(engine, "UPDATE users SET role = 'admin' WHERE id = :u RETURNING id", u=user)
    return user


def post(client: TestClient, headers: dict[str, str], path: str, key: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(path, headers=headers | {"Idempotency-Key": key or uuid.uuid4().hex})


def test_start_replay_and_second_start(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, admin(engine))
    key = uuid.uuid4().hex

    started = post(client, headers, "/api/v1/runs", key)
    replayed = post(client, headers, "/api/v1/runs", key)
    second = post(client, headers, "/api/v1/runs")

    assert started.status_code == 202
    RUN_SCHEMA.validate(started.json())
    assert (started.json()["kind"], started.json()["status"], started.json()["can_start"], started.json()["can_stop"]) == ("manual", "queued", False, True)
    assert replayed.status_code == 202 and replayed.json() == started.json() and replayed.headers["Idempotency-Replayed"] == "true"
    assert second.status_code == 409
    ERROR_SCHEMA.validate(second.json())
    assert second.json()["error"]["code"] == "RUN_IN_PROGRESS" and second.json()["error"]["details"]["run"]["run_id"] == started.json()["id"]
    current = client.get("/api/v1/runs/current", headers=headers).json()
    RUN_SCHEMA.validate(current)
    assert current["id"] == started.json()["id"]


def test_stop_by_admin(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, admin(engine))
    run_id = post(client, headers, "/api/v1/runs").json()["id"]

    stopped = post(client, headers, f"/api/v1/runs/{run_id}/stop")

    assert stopped.status_code == 202
    RUN_SCHEMA.validate(stopped.json())
    assert stopped.json()["status"] == "finished" and stopped.json()["can_start"] is True  # выданных заданий не было — сразу завершён
    assert post(client, headers, f"/api/v1/runs/{run_id}/stop").status_code == 404
    assert post(client, headers, "/api/v1/runs/999999999/stop").status_code == 404


def test_user_sees_status_but_cannot_start_or_stop(client: TestClient, engine: Engine) -> None:
    run_id = post(client, bearer(client, engine, admin(engine)), "/api/v1/runs").json()["id"]
    headers = bearer(client, engine, user_with_list(engine, items=0))

    current = client.get("/api/v1/runs/current", headers=headers).json()

    assert (current["id"], current["can_start"], current["can_stop"]) == (run_id, False, False)
    assert post(client, headers, "/api/v1/runs").status_code == 403
    assert post(client, headers, f"/api/v1/runs/{run_id}/stop").status_code == 403
    assert run_sql(engine, "SELECT status FROM parse_runs WHERE id = :r", r=run_id) == "queued"


def test_changes_need_key_and_token(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, admin(engine))

    assert client.post("/api/v1/runs", headers=headers).status_code == 422
    assert client.post("/api/v1/runs", headers={"Idempotency-Key": uuid.uuid4().hex}).status_code == 401
    assert client.get("/api/v1/runs/current").status_code == 401
