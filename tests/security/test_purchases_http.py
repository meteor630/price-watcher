"""/api/v1/purchases и /archive: ответ по схеме, страницы с курсором, чужие данные не видны, строгие параметры."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, text
from test_dashboard_http import NOW, OPENAPI, bearer, client, engine, run_sql, schema, user_with_list  # noqa: F401 — фикстуры модуля

PAGE_SCHEMA = schema("PurchasesOverview")
ARCHIVE_SCHEMA = Draft202012Validator(
    OPENAPI["paths"]["/archive"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] | {"components": OPENAPI["components"]}
)


def items_of(engine: Engine, user: int) -> list[int]:
    with engine.connect() as db:
        return [row[0] for row in db.execute(text("SELECT id FROM items WHERE user_id = :u ORDER BY id"), {"u": user})]


def buy(engine: Engine, item: int, price: int, days_ago: int) -> None:
    run_sql(engine, "UPDATE items SET state = 'purchased', purchased_at = :t, purchase_price_minor = :p, "
                    "purchase_quantity = 1, purchase_baseline_minor = 100000 WHERE id = :i RETURNING id", t=NOW - timedelta(days=days_ago), p=price, i=item)


def test_purchases_match_contract_and_page(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=3)
    headers = bearer(client, engine, user)
    first, second, third = items_of(engine, user)
    buy(engine, first, 90_000, 0)
    buy(engine, second, 120_000, 1)
    buy(engine, third, 50_000, 400)  # за год не входит

    whole = client.get("/api/v1/purchases", headers=headers)
    page = client.get("/api/v1/purchases", params={"limit": 1}, headers=headers).json()
    rest = client.get("/api/v1/purchases", params={"limit": 1, "cursor": page["next_cursor"]}, headers=headers).json()

    assert whole.status_code == 200
    PAGE_SCHEMA.validate(whole.json())
    body = whole.json()
    assert (body["period"], body["count"], body["spent_minor"], body["savings"]) == ("month", 2, 210_000, {"savings_minor": -10_000, "savings_text": "100\xa0₽"})
    assert [entry["purchase"]["item_id"] for entry in page["items"] + rest["items"]] == [first, second] and rest["next_cursor"] is None
    assert client.get("/api/v1/purchases", params={"period": "year"}, headers=headers).json()["count"] == 2


def test_archive_matches_contract(client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=2)
    headers = bearer(client, engine, user)
    first, _ = items_of(engine, user)
    run_sql(engine, "UPDATE items SET state = 'archived', archived_at = :t WHERE id = :i RETURNING id", t=NOW, i=first)

    response = client.get("/api/v1/archive", headers=headers)

    assert response.status_code == 200
    ARCHIVE_SCHEMA.validate(response.json())
    assert [entry["item"]["id"] for entry in response.json()["items"]] == [first] and response.json()["next_cursor"] is None


def test_someone_elses_purchases_are_not_visible(client: TestClient, engine: Engine) -> None:
    owner, other = user_with_list(engine, items=1), user_with_list(engine, items=0)
    buy(engine, items_of(engine, owner)[0], 1_000, 0)

    body = client.get("/api/v1/purchases", headers=bearer(client, engine, other)).json()

    assert (body["count"], body["items"], body["categories"]) == (0, [], [])


@pytest.mark.parametrize(("path", "params"), [("/api/v1/purchases", {"period": "century"}), ("/api/v1/purchases", {"cursor": "x1"}),
                                              ("/api/v1/archive", {"limit": 0}), ("/api/v1/archive", {"cursor": "o-1"})])
def test_bad_parameters_are_refused(client: TestClient, engine: Engine, path: str, params: dict) -> None:
    assert client.get(path, params=params, headers=bearer(client, engine, user_with_list(engine, items=0))).status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/purchases", "/api/v1/archive"])
def test_needs_app_token(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401
