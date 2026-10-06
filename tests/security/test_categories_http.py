"""GET /api/v1/categories/{id} и /chart: ответ по схеме контракта, ETag, диапазоны, чужая категория — 404, только токен приложения."""

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, text
from test_dashboard_http import OPENAPI, bearer, client, engine, run_sql, schema, user_with_list  # noqa: F401 — фикстуры модуля

CHART_SCHEMA = Draft202012Validator(
    OPENAPI["components"]["responses"]["Chart"]["content"]["application/json"]["schema"] | {"components": OPENAPI["components"]}
)


def category_of(engine: Engine, user: int) -> int:
    with engine.connect() as db:
        return db.execute(text("SELECT id FROM categories WHERE user_id = :u"), {"u": user}).scalar_one()


def test_category_matches_contract_with_etag(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=2)
    headers = bearer(client, engine, user)
    path = f"/api/v1/categories/{category_of(engine, user)}"

    response = client.get(path, headers=headers)

    assert response.status_code == 200
    body = response.json()
    schema("CategoryDetail").validate(body)
    assert body["groups"][0]["id"] is None and len(body["groups"][0]["items"]) == 2
    assert body["groups"][0]["items"][0]["unit_price_text"] == "900\xa0₽"
    assert client.get(path, headers=headers | {"If-None-Match": response.headers["ETag"]}).status_code == 304


@pytest.mark.parametrize(("chart_range", "points"), [("week", 7), ("month", 30), ("year", 365)])
def test_chart_ranges_match_contract(client: TestClient, engine: Engine, chart_range: str, points: int) -> None:
    user = user_with_list(engine, items=1)

    body = client.get(f"/api/v1/categories/{category_of(engine, user)}/chart", params={"range": chart_range}, headers=bearer(client, engine, user)).json()

    CHART_SCHEMA.validate(body)
    assert body["range"] == chart_range and len(body["points"]) == points


def test_someone_elses_category_is_not_found(client: TestClient, engine: Engine) -> None:
    category = category_of(engine, user_with_list(engine, items=0))
    stranger = run_sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES ('x' || md5(random()::text), 'h', 'user', 'S') RETURNING id")
    headers = bearer(client, engine, stranger)

    for path in (f"/api/v1/categories/{category}", f"/api/v1/categories/{category}/chart"):
        response = client.get(path, headers=headers)
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("path", ["/api/v1/categories/0", "/api/v1/categories/abc", "/api/v1/categories/1/chart?range=day"])
def test_bad_parameters_are_refused(client: TestClient, engine: Engine, path: str) -> None:
    response = client.get(path, headers=bearer(client, engine, user_with_list(engine, items=0)))

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_category_needs_app_token(client: TestClient) -> None:
    assert client.get("/api/v1/categories/1").status_code == 401


# --- порядок категорий ---

import uuid  # noqa: E402


def categories_with_order(engine: Engine, count: int = 3) -> tuple[int, list[int], int]:
    user = run_sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES ('o' || md5(random()::text), 'h', 'user', 'O') RETURNING id")
    ids = [run_sql(engine, "INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, :n, :n, :p) RETURNING id", u=user, n=f"к{index}", p=index) for index in range(count)]
    version = run_sql(engine, "SELECT categories_order_version FROM users WHERE id = :u", u=user)
    return user, ids, version


def reorder(client: TestClient, headers: dict, body: dict, key: str | None = None):  # type: ignore[no-untyped-def]
    return client.put("/api/v1/categories/order", json=body, headers=headers | {"Idempotency-Key": key or uuid.uuid4().hex})


def test_reorder_matches_contract_and_replays(client: TestClient, engine: Engine) -> None:
    user, ids, version = categories_with_order(engine)
    headers = bearer(client, engine, user)
    key = uuid.uuid4().hex
    body = {"ids": list(reversed(ids)), "version": version}

    first, again = reorder(client, headers, body, key), reorder(client, headers, body, key)

    assert first.status_code == 200 and first.json() == again.json() == {"ids": list(reversed(ids)), "version": version + 1}
    schema("OrderState").validate(first.json())
    assert [card["id"] for card in client.get("/api/v1/dashboard", headers=headers).json()["categories"]] == list(reversed(ids))


def test_reorder_with_old_version_conflicts(client: TestClient, engine: Engine) -> None:
    user, ids, version = categories_with_order(engine)
    headers = bearer(client, engine, user)
    reorder(client, headers, {"ids": ids[::-1], "version": version})

    response = reorder(client, headers, {"ids": ids, "version": version})

    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert response.json()["error"]["details"]["current"] == {"ids": ids[::-1], "version": version + 1}  # актуальный порядок


@pytest.mark.parametrize("make_body", [lambda ids, v: {"ids": ids[:-1], "version": v}, lambda ids, v: {"ids": ids + ids[:1], "version": v},
                                       lambda ids, v: {"ids": [], "version": v}, lambda ids, v: {"ids": ids, "version": "1"},
                                       lambda ids, v: {"ids": ids, "version": v, "extra": 1}])
def test_bad_reorder_is_refused(client: TestClient, engine: Engine, make_body) -> None:  # type: ignore[no-untyped-def]
    user, ids, version = categories_with_order(engine)

    response = reorder(client, bearer(client, engine, user), make_body(ids, version))

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_reorder_needs_key_and_own_categories(client: TestClient, engine: Engine) -> None:
    user, ids, version = categories_with_order(engine)
    _, foreign, _ = categories_with_order(engine, count=1)
    headers = bearer(client, engine, user)

    assert client.put("/api/v1/categories/order", json={"ids": ids, "version": version}, headers=headers).status_code == 422
    assert reorder(client, headers, {"ids": ids[:-1] + foreign, "version": version}).status_code == 422
