"""Управление каталогом и права нативного клиента."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_dashboard_http import bearer, client, engine, run_sql, user_with_list  # noqa: F401


def write(client: TestClient, headers: dict, method: str, path: str, body: dict, key: str | None = None):
    return client.request(method, f"/api/v1/catalog/{path}", headers=headers | {"Idempotency-Key": key or str(uuid4())}, json=body)


def test_native_item_can_be_created_edited_deleted_and_restored(client: TestClient, engine: Engine) -> None:
    auth = bearer(client, engine, user_with_list(engine, items=0))
    categories = client.get("/api/v1/catalog/categories", headers=auth).json()
    category = categories["categories"][0]["category_id"]

    group = write(client, auth, "POST", f"categories/{category}/groups", {"name": "Кухня"})
    assert group.status_code == 201
    fields = {"category_id": category, "group_id": group.json()["id"], "name": "Чайник", "full_name": "", "quantity": 2}
    key = str(uuid4())

    added = write(client, auth, "POST", "items", fields, key)
    assert added.status_code == 201
    assert write(client, auth, "POST", "items", fields, key).json() == added.json()
    item_id = added.json()["id"]
    detail = client.get(f"/api/v1/items/{item_id}", headers=auth).json()
    assert detail["name"] == "Чайник" and detail["quantity"] == 2

    edit = fields | {"version": detail["version"], "name": "Стальной чайник"}
    assert write(client, auth, "PATCH", f"items/{item_id}", edit).status_code == 200
    assert write(client, auth, "PATCH", f"items/{item_id}", edit).status_code == 409
    [listed] = client.get("/api/v1/catalog/items", headers=auth).json()["items"]
    assert listed["product"]["name"] == "Стальной чайник"

    removed = write(client, auth, "POST", f"items/{item_id}/remove", {"version": listed["product"]["version"]})
    assert removed.status_code == 200
    assert client.get("/api/v1/catalog/items", headers=auth).json() == {"items": []}
    assert write(client, auth, "POST", "undo", {"token": removed.json()["undo_token"]}).status_code == 200
    assert client.get(f"/api/v1/items/{item_id}", headers=auth).status_code == 200


def test_malformed_payload_and_foreign_entity_are_rejected(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    item_id = run_sql(engine, "SELECT id FROM items WHERE user_id = :owner", owner=user)
    auth = bearer(client, engine, user_with_list(engine, items=0))

    assert client.get("/api/v1/catalog/items").status_code == 401
    assert client.get(f"/api/v1/catalog/items/{item_id}/links", headers=auth).status_code == 404
    assert write(client, auth, "POST", f"items/{item_id}/remove", {"version": 1}).status_code == 404
    assert write(client, auth, "POST", f"items/{item_id}/remove", {"version": "1"}).status_code == 422
    assert write(client, auth, "POST", "categories", {"name": "Дом", "extra": True}).status_code == 422
    assert client.get("/api/v1/manage/users", headers=auth).status_code == 403
    assert client.get("/api/v1/manage/stores", headers=auth).status_code == 403
    assert client.get("/api/v1/manage/schedule", headers=auth).status_code == 403


def test_total_chart_gives_daily_values_for_this_account(client: TestClient, engine: Engine) -> None:
    auth = bearer(client, engine, user_with_list(engine, items=2))
    user_with_list(engine, items=1)

    response = client.get("/api/v1/dashboard/chart", params={"range": "week"}, headers=auth)
    assert response.status_code == 200
    points = response.json()["points"]
    assert len(points) == 7
    assert [point["value_minor"] for point in points[-2:]] == [200_001, 180_001]


def test_pairing_code_is_not_returned_on_idempotency_replay(client: TestClient, engine: Engine) -> None:
    auth = bearer(client, engine, user_with_list(engine, items=0))
    headers = auth | {"Idempotency-Key": str(uuid4())}
    first = client.post("/api/v1/profile/pairing-code", headers=headers)
    retry = client.post("/api/v1/profile/pairing-code", headers=headers)
    assert first.status_code == 200 and len(first.json()["code"]) == 8
    assert retry.status_code == 409 and "code" not in retry.json()


def test_native_purchase_archive_and_return_share_the_site_history(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    item_id = run_sql(engine, "SELECT id FROM items WHERE user_id = :owner", owner=user)
    auth = bearer(client, engine, user)
    path = f"items/{item_id}"
    form = client.get(f"/api/v1/catalog/{path}/purchase", headers=auth).json()
    purchase = {"version": form["version"], "quantity": 1, "price": "900", "store_id": form["store_id"], "day": form["today"]}

    assert write(client, auth, "POST", f"{path}/purchase", purchase).status_code == 200
    detail = client.get(f"/api/v1/{path}", headers=auth).json()
    assert detail["state"] == "purchased" and detail["purchase"]["price_minor"] == 90_000
    assert client.get("/api/v1/purchases", headers=auth).json()["count"] == 1

    assert write(client, auth, "POST", f"{path}/return", {"version": detail["version"]}).status_code == 200
    version = client.get(f"/api/v1/{path}", headers=auth).json()["version"]
    assert write(client, auth, "POST", f"{path}/archive", {"version": version}).status_code == 200
    assert client.get("/api/v1/archive", headers=auth).json()["items"][0]["item"]["id"] == item_id
