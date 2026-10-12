"""Страницы входа, регистрации и выхода на настоящей базе (SITE_DESIGN_SPEC.md, раздел 14).

Обычный путь, граничные случаи и отказы: подделка формы с чужого сайта, XSS в возвращённых полях,
недействительное приглашение, неверные поля без траты приглашения, выход только с CSRF, коды не в журнале.
"""

import logging
import re
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.api.web_auth import CSRF_HEADER, SESSION_COOKIE
from pricewatch.application.accounts import create_invite
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.web_auth import csrf_token_for
from pricewatch.config.settings import Settings
from pricewatch.domain.accounts import Role
from pricewatch.media.store import MediaStore
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

ORIGIN = "https://prices.example.ru"
KEY = "web-pages-test-key-" + "k" * 20
PASSWORD = "длинный пароль 2026"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
HASHER = PasswordHasher(time_cost=1, memory_cost=8 * 1024)
FORM = {"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def client(engine: Engine) -> TestClient:
    settings = Settings(env="test", public_origin=ORIGIN, database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), HASHER, lambda: NOW, MediaStore(Path(tempfile.mkdtemp())), LinkCatalog())
    return TestClient(create_app(settings, context), base_url="https://testserver")


def account(engine: Engine, role: str = "user") -> str:
    name = f"p{uuid.uuid4().hex[:10]}"
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, :h, :r, :n)"),
            {"l": name, "h": HASHER.hash(PASSWORD), "r": role, "n": name.upper()},
        )
    return name


def invite(engine: Engine, at: datetime = NOW) -> str:
    admin = account(engine, "admin")
    with engine.connect() as db:
        admin_id = db.execute(text("SELECT id FROM users WHERE login = :l"), {"l": admin}).scalar_one()
    return create_invite(SqlUnitOfWork(engine), KEY.encode(), admin_id, Role.USER, at).code


def sign_in(client: TestClient, login: str, password: str = PASSWORD, headers: dict | None = None):  # type: ignore[no-untyped-def]
    return client.post("/web/login", data={"login": login, "password": password}, headers=headers or FORM, follow_redirects=False)


def no_inline_code(html: str) -> None:
    """Страница совместима с CSP: скрипты только файлами, без style= и обработчиков on…=."""
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+=", html)


# --- вход ---


def test_login_page_is_html_without_inline_code(client: TestClient) -> None:
    response = client.get("/web/login")

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert 'autocomplete="current-password"' in response.text
    no_inline_code(response.text)


def test_root_goes_to_site(client: TestClient) -> None:
    assert client.get("/", follow_redirects=False).headers["location"] == "/web/"


def test_successful_login_sets_session_and_goes_home(client: TestClient, engine: Engine) -> None:
    response = sign_in(client, account(engine))

    # Сначала проверка, что браузер сохранил вход; сохранил — на главную.
    assert response.status_code == 303 and response.headers["location"] == "/web/login/check"
    assert response.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=")
    assert client.get("/web/login/check", follow_redirects=False).headers["location"] == "/web/"
    assert client.get("/web/login", follow_redirects=False).headers["location"] == "/web/"  # уже вошёл


def test_browser_that_dropped_the_cookie_gets_an_explanation(client: TestClient, engine: Engine) -> None:
    # Safari по http:// или запрет cookie: пароль верный, но сессия не вернулась — не молчаливый возврат на вход.
    sign_in(client, account(engine))
    client.cookies.clear()

    check = client.get("/web/login/check", follow_redirects=False)
    page = client.get(check.headers["location"])

    assert check.status_code == 303 and check.headers["location"] == "/web/login?cookie=missing"
    assert page.status_code == 200 and "браузер не сохранил вход" in page.text and "https://" in page.text
    assert "браузер не сохранил вход" not in client.get("/web/login").text


def test_wrong_password_and_unknown_login_look_the_same(client: TestClient, engine: Engine) -> None:
    wrong = sign_in(client, account(engine), "не тот пароль")
    unknown = sign_in(client, f"nobody{uuid.uuid4().hex[:6]}")

    for response in (wrong, unknown):
        assert response.status_code == 401
        assert "Неверный логин или пароль." in response.text
        assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_too_many_attempts_shows_wait_time(client: TestClient, engine: Engine) -> None:
    login = account(engine)
    for _ in range(5):
        sign_in(client, login, "не тот пароль")

    response = sign_in(client, login)

    assert response.status_code == 429
    assert "Попробуйте снова через 15 мин." in response.text
    assert response.headers["Retry-After"] == "900"


def test_returned_login_is_escaped(client: TestClient) -> None:
    response = sign_in(client, '"><script>alert(1)</script>')

    assert "<script>alert(1)</script>" not in response.text
    assert "&#34;&gt;&lt;script&gt;" in response.text


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_login_form_from_other_site_is_refused(client: TestClient, engine: Engine, origin: str | None) -> None:
    headers = {"Content-Type": "application/x-www-form-urlencoded"} | ({"Origin": origin} if origin else {})

    response = sign_in(client, account(engine), headers=headers)

    assert response.status_code == 403
    assert 'data-error-code="CSRF_FAILED"' in response.text
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("login=a&login=b&password=x", "application/x-www-form-urlencoded"),  # повтор поля
        ('{"login": "a"}', "application/json"),
        ("login=" + "a" * 20_000, "application/x-www-form-urlencoded"),
    ],
    ids=["повтор", "json", "большая"],
)
def test_odd_forms_are_refused(client: TestClient, body: str, content_type: str) -> None:
    response = client.post("/web/login", content=body, headers={"Origin": ORIGIN, "Content-Type": content_type})

    assert response.status_code == 422
    assert 'data-error-code="VALIDATION_ERROR"' in response.text


# --- регистрация ---


def register(client: TestClient, code: str, login: str, password: str = PASSWORD, repeat: str | None = None):  # type: ignore[no-untyped-def]
    data = {"invite": code, "login": login, "password": password, "password_repeat": password if repeat is None else repeat}
    return client.post("/web/register", data=data, headers=FORM, follow_redirects=False)


def test_registration_by_invite_then_login(client: TestClient, engine: Engine) -> None:
    code = invite(engine)
    login = f"new{uuid.uuid4().hex[:8]}"

    page = client.get("/web/register", params={"invite": code})
    response = register(client, code, login)

    assert page.status_code == 200 and 'name="password_repeat"' in page.text
    no_inline_code(page.text)
    assert response.status_code == 303 and response.headers["location"] == "/web/login?registered=1"
    assert "Регистрация завершена" in client.get("/web/login?registered=1").text
    assert sign_in(client, login).status_code == 303
    assert register(client, code, f"other{uuid.uuid4().hex[:6]}").status_code == 400  # приглашение одноразовое


@pytest.mark.parametrize("code", ["", "выдуманный", "A" * 43])
def test_invalid_invite_shows_explanation_without_form(client: TestClient, code: str) -> None:
    response = client.get("/web/register", params={"invite": code})

    assert response.status_code == 400
    assert "Приглашение недействительно" in response.text
    assert "<form" not in response.text


def test_expired_invite_shows_explanation(client: TestClient, engine: Engine) -> None:
    code = invite(engine, at=NOW - timedelta(days=8))

    assert "<form" not in client.get("/web/register", params={"invite": code}).text


@pytest.mark.parametrize(
    ("login", "password", "repeat", "field", "message"),
    [
        ("x", PASSWORD, None, "login", "3–64 символа"),
        ("goodlogin", "короткий", None, "password", "не короче 10"),
        ("goodlogin", PASSWORD, "другой пароль 2026", "password_repeat", "Пароли не совпадают."),
    ],
    ids=["логин", "пароль", "повтор"],
)
def test_bad_fields_are_explained_and_invite_kept(
    client: TestClient, engine: Engine, login: str, password: str, repeat: str | None, field: str, message: str
) -> None:
    code = invite(engine)

    response = register(client, code, login, password, repeat)

    assert response.status_code in (200, 422)
    assert message in response.text
    assert f'id="{"repeat" if field == "password_repeat" else field}-error"' in response.text
    assert register(client, code, f"ok{uuid.uuid4().hex[:8]}").status_code == 303  # приглашение не потрачено


def test_taken_login_is_explained(client: TestClient, engine: Engine) -> None:
    existing = account(engine)

    response = register(client, invite(engine), existing.upper())

    assert response.status_code == 409 and "Такой логин уже занят" in response.text


def test_register_form_from_other_site_is_refused(client: TestClient, engine: Engine) -> None:
    code = invite(engine)
    data = {"invite": code, "login": "attacker1", "password": PASSWORD, "password_repeat": PASSWORD}

    response = client.post("/web/register", data=data, headers={"Origin": "https://evil.example"}, follow_redirects=False)

    assert response.status_code == 403
    assert register(client, code, f"ok{uuid.uuid4().hex[:8]}").status_code == 303


def test_invite_code_and_password_never_logged(client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture) -> None:
    code = invite(engine)
    with caplog.at_level(logging.DEBUG):
        client.get("/web/register", params={"invite": code})
        register(client, code, f"log{uuid.uuid4().hex[:8]}")

    # Только журнал сервера: тестовый HTTP-клиент сам пишет адреса запросов, в рабочем сервере его нет.
    logged = "\n".join(str(vars(record)) for record in caplog.records if record.name.startswith(("pricewatch", "uvicorn")))
    assert any(record.name == "pricewatch.api.edge" for record in caplog.records)
    assert code not in logged and PASSWORD not in logged


# --- выход ---


def test_logout_needs_csrf_then_revokes_session(client: TestClient, engine: Engine) -> None:
    sign_in(client, account(engine))
    token = client.cookies[SESSION_COOKIE]
    htmx = {"HX-Request": "true", "Origin": ORIGIN}

    refused = client.post("/web/logout", headers=htmx)
    done = client.post("/web/logout", headers=htmx | {CSRF_HEADER: csrf_token_for(KEY.encode(), token)})

    assert refused.status_code == 403 and 'data-error-code="CSRF_FAILED"' in refused.text
    assert refused.headers["HX-Retarget"] == "#flash"
    assert done.status_code == 204 and done.headers["HX-Redirect"] == "/web/login"
    assert 'pw_session=""' in done.headers["set-cookie"] or "Max-Age=0" in done.headers["set-cookie"]
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/web/login", follow_redirects=False).status_code == 200  # сессия отозвана: снова форма входа


# --- ошибки сайта ---


def test_htmx_error_replaces_callers_select_with_message(client: TestClient, engine: Engine) -> None:
    # Ошибка уходит в #flash, а hx-select вызвавшего элемента (#page-rest, #hero-figures) в ней ничего не находит —
    # без своего выбора сообщение оказалось бы пустым. HX-Reselect выбирает само сообщение.
    sign_in(client, account(engine))

    refused = client.post("/web/logout", headers={"HX-Request": "true", "Origin": ORIGIN})

    assert refused.headers["HX-Reselect"] == "[data-error-code]"
    assert refused.text.startswith('<p class="alert alert--error" role="alert" data-error-code="CSRF_FAILED">')


def test_unknown_site_page_is_html_404(client: TestClient) -> None:
    response = client.get("/web/no-such-page")

    assert response.status_code == 404
    assert 'data-error-code="NOT_FOUND"' in response.text
    no_inline_code(response.text)


def test_api_errors_stay_json(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTH_REQUIRED"
