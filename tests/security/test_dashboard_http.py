"""GET /api/v1/dashboard и /dashboard/items: только токен приложения, ответ по схеме контракта, ETag, курсор, чужое не видно."""

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.app_auth import issue_pairing_code
from pricewatch.application.passwords import PasswordHasher
from pricewatch.config.settings import Settings
from pricewatch.media.store import MediaStore
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "dashboard-http-key-" + "k" * 24
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
OPENAPI = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi" / "v1.yaml").read_text(encoding="utf-8"))


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]})


PAGE_SCHEMA = Draft202012Validator(
    OPENAPI["paths"]["/dashboard/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    | {"components": OPENAPI["components"]}
)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def client(engine: Engine) -> TestClient:
    settings = Settings(env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), PasswordHasher(time_cost=1, memory_cost=8192), lambda: NOW, MediaStore(Path(tempfile.mkdtemp())))
    return TestClient(create_app(settings, context), base_url="https://testserver")


def run_sql(engine: Engine, sql: str, **params: object) -> int:
    with engine.begin() as db:
        return db.execute(text(sql), params).scalar_one()


def user_with_list(engine: Engine, items: int = 3) -> int:
    """Пользователь с категорией и товарами; у каждого товара цена вчера и сегодня (подешевело)."""
    user = run_sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id", l=f"d{uuid.uuid4().hex[:10]}")
    category = run_sql(engine, "INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, 'Дом <b>', 'дом <b>', 0) RETURNING id", u=user)
    store = run_sql(engine, "INSERT INTO stores (canonical_host, display_name, color, parser_key, auto_created, min_delay_ms) VALUES (:h, 'Магазин', 'store-4', 'universal', true, 2000) RETURNING id", h=f"{uuid.uuid4().hex[:8]}.example")
    run = run_sql(engine, "INSERT INTO parse_runs (kind, status, total, created_at, finished_at) VALUES ('on_add', 'finished', 0, :t, :t) RETURNING id", t=NOW - timedelta(days=3))
    for index in range(items):
        item = run_sql(engine, "INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, :n, :p) RETURNING id", u=user, c=category, n=f"Товар {index}", p=index)
        link = run_sql(engine, "INSERT INTO tracked_links (store_id, normalized_url, region_code) VALUES (:s, :url, 'spb') RETURNING id", s=store, url=f"https://shop.example/{uuid.uuid4().hex}")
        run_sql(engine, "INSERT INTO item_links (user_id, item_id, tracked_link_id, store_id, valid_from) VALUES (:u, :i, :l, :s, :f) RETURNING id", u=user, i=item, l=link, s=store, f=NOW - timedelta(days=10))
        for at, price in ((NOW - timedelta(days=1), 1000_00 + index), (NOW - timedelta(hours=1), 900_00 + index)):
            task = run_sql(engine, "INSERT INTO check_tasks (run_id, tracked_link_id, status, finished_at, created_at) VALUES (:r, :l, 'done', :t, :t) RETURNING id", r=run, l=link, t=at)
            run_sql(engine, "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, availability, region_code, confirmation, source) VALUES (:l, :k, :t, 'ok', :p, 'in_stock', 'spb', 'confirmed', 'site_data') RETURNING id", l=link, k=task, t=at, p=price)
    return user


def bearer(client: TestClient, engine: Engine, user: int) -> dict[str, str]:
    code = issue_pairing_code(SqlUnitOfWork(engine), KEY.encode(), user, NOW).code
    tokens = client.post("/api/v1/auth/pair", json={"code": code, "device_name": "iPhone"}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_dashboard_matches_contract_and_has_etag(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, user_with_list(engine))

    response = client.get("/api/v1/dashboard", params={"period": "week"}, headers=headers)
    day = client.get("/api/v1/dashboard", params={"period": "day"}, headers=headers).json()

    assert response.status_code == 200
    body = response.json()
    schema("Dashboard").validate(body)
    assert body["period"] == "week" and body["categories"][0]["name"] == "Дом <b>"
    assert body["total_minor"] == 3 * 900_00 + 3
    assert (body["panels"]["cheaper"], body["change"]) == (0, None)  # неделю назад цен ещё не было
    assert day["panels"]["cheaper"] == 3 and day["change"]["direction"] == "down"
    assert body["reviews_pending"] is None and body["run"]["can_start"] is False
    assert isinstance(body["total_minor"], int) and len(body["categories"][0]["sparkline"]) == 30
    cached = client.get("/api/v1/dashboard", params={"period": "week"}, headers=headers | {"If-None-Match": response.headers["ETag"]})
    assert cached.status_code == 304 and cached.content == b""
    other_period = client.get("/api/v1/dashboard", params={"period": "day"}, headers=headers | {"If-None-Match": response.headers["ETag"]})
    assert other_period.status_code == 200


def test_panel_pages_follow_cursor(client: TestClient, engine: Engine) -> None:
    headers = bearer(client, engine, user_with_list(engine, items=3))

    first = client.get("/api/v1/dashboard/items", params={"panel": "cheaper", "limit": 2}, headers=headers).json()
    second = client.get("/api/v1/dashboard/items", params={"panel": "cheaper", "limit": 2, "cursor": first["next_cursor"]}, headers=headers).json()

    for page in (first, second):
        PAGE_SCHEMA.validate(page)
    assert len(first["items"]) == 2 and len(second["items"]) == 1 and second["next_cursor"] is None
    assert {item["item_id"] for item in first["items"]}.isdisjoint(item["item_id"] for item in second["items"])


@pytest.mark.parametrize(
    "params",
    [{"panel": "everything"}, {"panel": "tracked", "cursor": "1"}, {"panel": "tracked", "cursor": "o-1"}, {"panel": "tracked", "limit": 0},
     {"panel": "tracked", "limit": 101}, {"panel": "tracked", "period": "decade"}],
)
def test_bad_parameters_are_refused(client: TestClient, engine: Engine, params: dict) -> None:
    response = client.get("/api/v1/dashboard/items", params=params, headers=bearer(client, engine, user_with_list(engine, items=0)))

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_someone_elses_list_is_not_visible(client: TestClient, engine: Engine) -> None:
    user_with_list(engine)
    stranger = run_sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'S') RETURNING id", l=f"s{uuid.uuid4().hex[:10]}")
    headers = bearer(client, engine, stranger)

    body = client.get("/api/v1/dashboard", headers=headers).json()
    items = client.get("/api/v1/dashboard/items", params={"panel": "tracked"}, headers=headers).json()

    assert (body["categories"], body["total_minor"], items["items"]) == ([], 0, [])


@pytest.mark.parametrize("path", ["/api/v1/dashboard", "/api/v1/dashboard/items?panel=tracked"])
def test_dashboard_needs_app_token(client: TestClient, path: str) -> None:
    client.cookies.set("pw_session", "some-session")

    response = client.get(path)

    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTH_REQUIRED"
