"""/api/v1/reviews: свои карточки по схеме, решение с ключом повтора, чужое и решённое — 404, строгое тело."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine
from test_dashboard_http import NOW, OPENAPI, bearer, client, engine, run_sql, schema, user_with_list  # noqa: F401 — фикстуры модуля

LIST_SCHEMA = Draft202012Validator(
    OPENAPI["paths"]["/reviews"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] | {"components": OPENAPI["components"]}
)


def pending_jump(engine: Engine, user: int) -> int:
    link = run_sql(engine, "SELECT tracked_link_id FROM item_links WHERE user_id = :u ORDER BY id LIMIT 1", u=user)
    run = run_sql(engine, "INSERT INTO parse_runs (kind, status, total, created_at, finished_at) VALUES ('on_add', 'finished', 0, :t, :t) RETURNING id", t=NOW)
    task = run_sql(engine, "INSERT INTO check_tasks (run_id, tracked_link_id, status, finished_at, created_at) VALUES (:r, :l, 'done', :t, :t) RETURNING id",
                   r=run, l=link, t=NOW - timedelta(minutes=10))
    return run_sql(engine, "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, availability, region_code, confirmation, source) "
                           "VALUES (:l, :k, :t, 'ok', 500000, 'in_stock', 'spb', 'pending', 'site_data') RETURNING id", l=link, k=task, t=NOW - timedelta(minutes=10))


def decide(client: TestClient, headers: dict[str, str], review: int, body: object, key: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/reviews/{review}/decision", json=body, headers=headers | {"Idempotency-Key": key or uuid.uuid4().hex})


def test_list_and_accept_with_replay(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(client, engine, user)
    review = pending_jump(engine, user)
    key = uuid.uuid4().hex

    listing = client.get("/api/v1/reviews", headers=headers)
    accepted = decide(client, headers, review, {"decision": "accept"}, key)
    replayed = decide(client, headers, review, {"decision": "accept"}, key)

    LIST_SCHEMA.validate(listing.json())
    [card] = listing.json()["items"]
    assert (card["id"], card["kind"], card["kinds"], card["decision"]) == (review, "price_jump", ["price_jump"], None)
    assert accepted.status_code == 200
    schema("Review").validate(accepted.json())
    assert accepted.json()["decision"] == "accept" and replayed.json() == accepted.json()
    assert client.get("/api/v1/reviews", headers=headers).json()["items"] == []
    assert decide(client, headers, review, {"decision": "reject"}).status_code == 404  # уже решено
    assert client.get("/api/v1/dashboard", headers=headers).json()["reviews_pending"] == 0


def test_someone_elses_review_is_not_visible(client: TestClient, engine: Engine) -> None:
    review = pending_jump(engine, user_with_list(engine, items=1))
    headers = bearer(client, engine, user_with_list(engine, items=1))

    assert client.get("/api/v1/reviews", headers=headers).json()["items"] == []
    assert decide(client, headers, review, {"decision": "accept"}).status_code == 404


@pytest.mark.parametrize("body", [{"decision": "maybe"}, {"decision": "accept", "extra": 1}, {}, ["accept"], {"decision": True}])
def test_strict_body(client: TestClient, engine: Engine, body: object) -> None:
    user = user_with_list(engine, items=1)

    assert decide(client, bearer(client, engine, user), pending_jump(engine, user), body).status_code == 422


def test_needs_key_and_token(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(client, engine, user)
    review = pending_jump(engine, user)

    assert client.post(f"/api/v1/reviews/{review}/decision", json={"decision": "accept"}, headers=headers).status_code == 422
    assert client.get("/api/v1/reviews").status_code == 401
