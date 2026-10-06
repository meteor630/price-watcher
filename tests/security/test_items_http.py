"""/api/v1/items/{id}: ответ по схеме, ETag, PATCH с версией и ключом повтора, конфликт, строгие типы, чужой товар — 404."""

import uuid

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, text
from test_dashboard_http import OPENAPI, bearer, client, engine, run_sql, schema, user_with_list  # noqa: F401 — фикстуры модуля

CHART_SCHEMA = Draft202012Validator(
    OPENAPI["components"]["responses"]["Chart"]["content"]["application/json"]["schema"] | {"components": OPENAPI["components"]}
)
ERROR_SCHEMA = schema("ErrorResponse")


def first_item(engine: Engine, user: int) -> tuple[int, int, int]:
    with engine.connect() as db:
        return tuple(db.execute(
            text("SELECT i.id, i.version, l.store_id FROM items i JOIN item_links l ON l.item_id = i.id WHERE i.user_id = :u ORDER BY i.id LIMIT 1"), {"u": user}
        ).one())  # type: ignore[return-value]


def patch(client: TestClient, headers: dict, item: int, body: dict, key: str | None = None):  # type: ignore[no-untyped-def]
    return client.patch(f"/api/v1/items/{item}", json=body, headers=headers | {"Idempotency-Key": key or uuid.uuid4().hex})


def test_item_matches_contract_with_etag(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(client, engine, user)
    item, _, _ = first_item(engine, user)

    response = client.get(f"/api/v1/items/{item}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    schema("ItemDetail").validate(body)
    assert body["offers"][0]["is_best"] is True and body["history"][0]["kind"] == "price_changed"
    assert client.get(f"/api/v1/items/{item}", headers=headers | {"If-None-Match": response.headers["ETag"]}).status_code == 304


def test_patch_changes_once_and_replays_same_answer(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(client, engine, user)
    item, version, _ = first_item(engine, user)
    key = uuid.uuid4().hex

    first = patch(client, headers, item, {"version": version, "quantity": 3}, key)
    again = patch(client, headers, item, {"version": version, "quantity": 3}, key)

    assert first.status_code == 200 and again.status_code == 200
    schema("ItemDetail").validate(first.json())
    assert first.json() == again.json() and again.headers["Idempotency-Replayed"] == "true"
    assert first.json()["quantity"] == 3 and first.json()["version"] == version + 1
    assert first_item(engine, user)[1] == version + 1  # изменение сделано один раз


def test_same_key_with_other_body_is_refused(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(client, engine, user)
    item, version, _ = first_item(engine, user)
    key = uuid.uuid4().hex
    patch(client, headers, item, {"version": version, "quantity": 2}, key)

    response = patch(client, headers, item, {"version": version + 1, "quantity": 5}, key)

    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_version_conflict_returns_current_item(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(client, engine, user)
    item, version, _ = first_item(engine, user)
    patch(client, headers, item, {"version": version, "in_total": False})
    key = uuid.uuid4().hex

    conflict = patch(client, headers, item, {"version": version, "quantity": 2}, key)
    retry = patch(client, headers, item, {"version": version, "quantity": 2}, key)  # ошибка ключ не занимает

    assert conflict.status_code == 409
    ERROR_SCHEMA.validate(conflict.json())
    current = conflict.json()["error"]["details"]["current"]
    schema("ItemDetail").validate(current)
    assert current["version"] == version + 1 and current["in_total"] is False
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "VERSION_CONFLICT"


@pytest.mark.parametrize(
    "body",
    [{"version": 1}, {"version": 1, "quantity": "2"}, {"version": 1, "quantity": 0}, {"version": 1, "quantity": 1000},
     {"version": 1, "in_total": "yes"}, {"version": 1, "quantity": 2, "name": "x"}, {"quantity": 2}, {"version": 1.0, "quantity": 2}],
)
def test_malformed_patch_is_refused(client: TestClient, engine: Engine, body: dict) -> None:
    user = user_with_list(engine, items=1)

    response = patch(client, bearer(client, engine, user), first_item(engine, user)[0], body)

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("key", [None, "short", "x" * 65])
def test_patch_needs_idempotency_key(client: TestClient, engine: Engine, key: str | None) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(client, engine, user) | ({"Idempotency-Key": key} if key else {})
    item, version, _ = first_item(engine, user)

    response = client.patch(f"/api/v1/items/{item}", json={"version": version, "quantity": 2}, headers=headers)

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert first_item(engine, user)[1] == version


def test_store_chart_matches_contract(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    item, _, store = first_item(engine, user)

    body = client.get(f"/api/v1/items/{item}/chart", params={"store_id": store, "range": "week"}, headers=bearer(client, engine, user)).json()

    CHART_SCHEMA.validate(body)
    assert len(body["points"]) == 7


def test_someone_elses_item_is_not_found(client: TestClient, engine: Engine) -> None:
    owner = user_with_list(engine, items=1)
    item, version, store = first_item(engine, owner)
    stranger = run_sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES ('x' || md5(random()::text), 'h', 'user', 'S') RETURNING id")
    headers = bearer(client, engine, stranger)

    responses = [
        client.get(f"/api/v1/items/{item}", headers=headers),
        client.get(f"/api/v1/items/{item}/chart", params={"store_id": store}, headers=headers),
        patch(client, headers, item, {"version": version, "quantity": 9}),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert first_item(engine, owner)[1] == version
