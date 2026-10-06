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
