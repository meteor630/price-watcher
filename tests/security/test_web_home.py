"""Главная сайта: только по сессии, данные экранированы, без встроенного кода, период и панели фрагментами, чужое не видно."""

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.passwords import PasswordHasher
from pricewatch.config.settings import Settings
from pricewatch.media.store import MediaStore
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

ORIGIN = "https://prices.example.ru"
KEY = "web-home-test-key-" + "k" * 24
PASSWORD = "длинный пароль 2026"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
HASHER = PasswordHasher(time_cost=1, memory_cost=8 * 1024)
EVIL = '<img src=x onerror=alert(1)>'


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def client(engine: Engine) -> TestClient:
    settings = Settings(env="test", public_origin=ORIGIN, database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), HASHER, lambda: NOW, MediaStore(Path(tempfile.mkdtemp())))
    return TestClient(create_app(settings, context), base_url="https://testserver")


def sql(engine: Engine, statement: str, **params: object) -> int:
    with engine.begin() as db:
        return db.execute(text(statement), params).scalar_one()


def account_with_list(engine: Engine, category: str = "Дом", items: int = 2) -> str:
    login = f"h{uuid.uuid4().hex[:10]}"
    user = sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, :h, 'user', :n) RETURNING id", l=login, h=HASHER.hash(PASSWORD), n=EVIL)
    category_id = sql(engine, "INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, :n, lower(:n), 0) RETURNING id", u=user, n=category)
    store = sql(engine, "INSERT INTO stores (canonical_host, display_name, color, parser_key, auto_created, min_delay_ms) VALUES (:h, :n, 'store-5', 'universal', true, 2000) RETURNING id", h=f"{uuid.uuid4().hex[:8]}.example", n=EVIL)
    run = sql(engine, "INSERT INTO parse_runs (kind, status, total, created_at, finished_at) VALUES ('on_add', 'finished', 0, :t, :t) RETURNING id", t=NOW)
    for index in range(items):
        item = sql(engine, "INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, :n, :p) RETURNING id", u=user, c=category_id, n=f"{EVIL} {index}", p=index)
        link = sql(engine, "INSERT INTO tracked_links (store_id, normalized_url, region_code) VALUES (:s, :url, 'spb') RETURNING id", s=store, url=f"https://shop.example/{uuid.uuid4().hex}")
        sql(engine, "INSERT INTO item_links (user_id, item_id, tracked_link_id, store_id, valid_from) VALUES (:u, :i, :l, :s, :f) RETURNING id", u=user, i=item, l=link, s=store, f=NOW - timedelta(days=20))
        for at, price in ((NOW - timedelta(days=1), 1000_00), (NOW - timedelta(hours=1), 1200_00 + index)):
            task = sql(engine, "INSERT INTO check_tasks (run_id, tracked_link_id, status, finished_at, created_at) VALUES (:r, :l, 'done', :t, :t) RETURNING id", r=run, l=link, t=at)
            sql(engine, "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, availability, region_code, confirmation, source) VALUES (:l, :k, :t, 'ok', :p, 'in_stock', 'spb', 'confirmed', 'site_data') RETURNING id", l=link, k=task, t=at, p=price)
    return login


def sign_in(client: TestClient, login: str) -> None:
    response = client.post("/web/login", data={"login": login, "password": PASSWORD}, headers={"Origin": ORIGIN}, follow_redirects=False)
    assert response.status_code == 303


class TagScan(HTMLParser):
    """Настоящие теги и атрибуты страницы; экранированный текст сюда не попадает."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {name for name, _ in attrs}
        if tag == "script" and "src" not in names:
            self.problems.append("встроенный скрипт")
        if tag in ("img", "iframe", "object", "embed"):
            self.problems.append(f"тег {tag}")
        self.problems += [f"{tag}[{name}]" for name in names if name == "style" or name.startswith("on")]


def no_inline_code(html: str) -> None:
    scan = TagScan()
    scan.feed(html)
    assert scan.problems == []


def test_home_needs_session(client: TestClient) -> None:
    response = client.get("/web/", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/web/login"


def test_home_shows_list_escaped_and_without_inline_code(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, category=f"Кат {EVIL}"))

    html = client.get("/web/").text

    assert EVIL not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    no_inline_code(html)
    assert "2\u202f400,01\xa0₽" in html  # 1200,00 + 1200,01
    assert 'data-category="' in html and '<svg class="sparkline"' in html
    assert 'hx-get="/web/?period=week"' in html and 'aria-current="true"' in html


def test_period_switch_and_panel_fragment(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine))

    week = client.get("/web/", params={"period": "week"}, headers={"HX-Request": "true"})
    panel = client.get("/web/dashboard/items", params={"panel": "pricier", "period": "day"}, headers={"HX-Request": "true"})

    assert week.status_code == 200 and 'id="hero-figures"' in week.text and 'id="dashboard-rest"' in week.text
    assert "за неделю" in week.text
    assert panel.status_code == 200 and panel.text.count('class="change-row"') == 2
    assert "Подорожали за сутки" in panel.text and EVIL not in panel.text
    no_inline_code(panel.text)


def test_panel_pages_append_with_more_button(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=25))

    first = client.get("/web/dashboard/items", params={"panel": "tracked"}).text
    rest = client.get("/web/dashboard/items", params={"panel": "tracked", "offset": 20}).text

    assert first.count('class="change-row"') == 20 and "Показать ещё" in first and "offset=20" in first
    assert rest.count('class="change-row"') == 5 and "Показать ещё" not in rest and "drawer__head" not in rest


@pytest.mark.parametrize(
    "path", ["/web/?period=decade", "/web/dashboard/items?panel=all", "/web/dashboard/items?panel=tracked&offset=-1"]
)
def test_bad_parameters_give_html_error(client: TestClient, engine: Engine, path: str) -> None:
    sign_in(client, account_with_list(engine, items=0))

    response = client.get(path)

    assert response.status_code == 422 and 'data-error-code="VALIDATION_ERROR"' in response.text


def test_someone_elses_list_is_not_shown(client: TestClient, engine: Engine) -> None:
    account_with_list(engine, category="Чужая категория")
    sign_in(client, account_with_list(engine, category="Моя категория", items=0))

    html = client.get("/web/").text

    assert "Моя категория" in html and "Чужая категория" not in html


def test_empty_list_has_empty_state(client: TestClient, engine: Engine) -> None:
    login = f"e{uuid.uuid4().hex[:10]}"
    sql(engine, "INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, :h, 'user', 'E') RETURNING id", l=login, h=HASHER.hash(PASSWORD))
    sign_in(client, login)

    html = client.get("/web/").text

    assert "Список пока пуст" in html and '<svg class="sparkline"' not in html
