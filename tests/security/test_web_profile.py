"""Профиль на сайте: код подключает приложение, Telegram привязывается кодом и отвязывается, устройства отзываются, смена пароля с ошибками у полей,
лимит попыток, остальные сессии выходят, текущая остаётся, CSRF."""

import re

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_web_home import EVIL, KEY, NOW, PASSWORD, account_with_list, client, engine, no_inline_code, sign_in, sql  # noqa: F401 — фикстуры модуля
from test_web_settings_items import htmx

from pricewatch.api.web_auth import SESSION_COOKIE
from pricewatch.application.telegram import link_chat
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

PAGE = "/web/settings/profile"
NEW_PASSWORD = "совсем новый пароль 2026"


def pair(client: TestClient, code: str, name: str = "iPhone Ани") -> dict[str, str]:
    response = client.post("/api/v1/auth/pair", json={"code": code, "device_name": name})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def code_from(html: str) -> str:
    match = re.search(r'aria-label="Код подключения: ([A-Z2-7 ]{15})"', html)
    assert match is not None
    return match.group(1).replace(" ", "")


def telegram_code_from(html: str) -> str:
    match = re.search(r"/start ([A-Za-z0-9_-]{22})</code>", html)
    assert match is not None
    return match.group(1)


def password_form(current: str = PASSWORD, new: str = NEW_PASSWORD, repeat: str | None = None) -> dict[str, str]:
    return {"current_password": current, "password": new, "password_repeat": new if repeat is None else repeat}


def test_page_and_pairing_code_connects_app(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=0))

    page = client.get(PAGE)
    issued = client.post("/web/profile/pairing-code", headers=htmx(client))
    app = pair(client, code_from(issued.text), name=EVIL)
    with_device = client.get(PAGE).text

    assert page.status_code == 200 and 'href="/web/settings/profile" aria-current="page"' in page.text and "Приложение ещё не подключено" in page.text
    no_inline_code(page.text)
    assert issued.status_code == 200 and "data-countdown=" in issued.text
    no_inline_code(issued.text)
    assert client.get("/api/v1/dashboard", headers=app).status_code == 200
    assert EVIL not in with_device and "&lt;img" in with_device  # имя устройства — недоверенный ввод


def test_revoke_device_cuts_app_access(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    app = pair(client, code_from(client.post("/web/profile/pairing-code", headers=htmx(client)).text))
    device = sql(engine, "SELECT d.id FROM devices d JOIN users u ON u.id = d.user_id WHERE u.login = :l", l=login)

    dialog = client.get(f"/web/devices/{device}/revoke").text
    revoked = client.post(f"/web/devices/{device}/revoke", headers=htmx(client))

    assert "<dialog" in dialog and "сразу выйдет" in dialog
    assert revoked.status_code == 200 and "Доступ устройства отозван." in revoked.text
    assert client.get("/api/v1/dashboard", headers=app).status_code == 401
    assert client.post(f"/web/devices/{device}/revoke", headers=htmx(client)).status_code == 404


def test_someone_elses_device_is_not_found(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=0))
    pair(client, code_from(client.post("/web/profile/pairing-code", headers=htmx(client)).text))
    device = sql(engine, "SELECT max(id) FROM devices")
    sign_in(client, account_with_list(engine, items=0))

    assert client.get(f"/web/devices/{device}/revoke").status_code == 404
    assert client.post(f"/web/devices/{device}/revoke", headers=htmx(client)).status_code == 404
    assert sql(engine, "SELECT count(*) FROM devices WHERE id = :d AND revoked_at IS NULL", d=device) == 1


def test_password_errors_are_shown_at_fields(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)

    wrong = client.post("/web/profile/password", data=password_form(current="не тот"), headers=htmx(client))
    short = client.post("/web/profile/password", data=password_form(new="коротко"), headers=htmx(client))
    mismatch = client.post("/web/profile/password", data=password_form(repeat="другой длинный пароль"), headers=htmx(client))

    assert wrong.status_code == 200 and "Текущий пароль неверный." in wrong.text and 'aria-invalid="true"' in wrong.text
    assert "Не короче 10 символов." in short.text
    assert "Пароли не совпадают." in mismatch.text
    no_inline_code(wrong.text)
    for _ in range(4):
        client.post("/web/profile/password", data=password_form(current="не тот"), headers=htmx(client))
    limited = client.post("/web/profile/password", data=password_form(), headers=htmx(client))
    assert limited.status_code == 429  # пять неверных — как на входе, даже с верным паролем ждём


def test_password_change_keeps_this_session_and_ends_others(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    other_session = client.cookies[SESSION_COOKIE]
    sign_in(client, login)  # вторая сессия — текущая
    app = pair(client, code_from(client.post("/web/profile/pairing-code", headers=htmx(client)).text))

    changed = client.post("/web/profile/password", data=password_form(), headers=htmx(client))

    assert changed.status_code == 200 and "Пароль изменён." in changed.text
    audit = sql(engine, "SELECT after_json->>'sessions_revoked' || '/' || (after_json->>'devices_revoked') FROM audit_events a JOIN users u ON u.id = a.actor_id "
                        "WHERE u.login = :l AND a.action = 'password_changed'", l=login)
    assert audit == "1/1"
    assert client.get(PAGE).status_code == 200
    assert client.get("/api/v1/dashboard", headers=app).status_code == 401
    current = client.cookies[SESSION_COOKIE]
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, other_session)
    assert client.get(PAGE, follow_redirects=False).headers["location"] == "/web/login"
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, current)
    assert client.post("/web/login", data={"login": login, "password": NEW_PASSWORD}, headers={"Origin": "https://prices.example.ru"},
                       follow_redirects=False).status_code == 303


def test_csrf_required(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=0))

    assert client.post("/web/profile/pairing-code", headers={"HX-Request": "true"}).status_code == 403
    assert client.post("/web/profile/password", data=password_form(), headers={"HX-Request": "true"}).status_code == 403
    assert client.post("/web/profile/telegram/code", headers={"HX-Request": "true"}).status_code == 403
    assert client.post("/web/profile/telegram/unlink", headers={"HX-Request": "true"}).status_code == 403


def test_telegram_code_links_and_unlinks(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    client.app.state.telegram_bot = None  # type: ignore[attr-defined]

    page = client.get(PAGE).text
    issued = client.post("/web/profile/telegram/code", headers=htmx(client))
    link_chat(SqlUnitOfWork(engine), KEY.encode(), telegram_code_from(issued.text), 700_000_000 + sql(engine, "SELECT id FROM users WHERE login = :l", l=login), NOW)
    linked = client.get(PAGE).text
    unlinked = client.post("/web/profile/telegram/unlink", headers=htmx(client))

    assert 'hx-post="/web/profile/telegram/code"' in page and "data-telegram-linked" not in page
    assert issued.status_code == 200 and "data-countdown=" in issued.text and "t.me/" not in issued.text
    no_inline_code(issued.text)
    assert "data-telegram-linked" in linked and 'hx-post="/web/profile/telegram/unlink"' in linked
    assert unlinked.status_code == 200 and "Telegram отвязан." in unlinked.text and "data-telegram-linked" not in unlinked.text
    assert sql(engine, "SELECT count(*) FROM users WHERE login = :l AND telegram_chat_id IS NULL", l=login) == 1


def test_telegram_code_opens_named_bot(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=0))
    client.app.state.telegram_bot = "price_watcher_bot"  # type: ignore[attr-defined]

    issued = client.post("/web/profile/telegram/code", headers=htmx(client)).text

    code = telegram_code_from(issued)
    assert f'href="https://t.me/price_watcher_bot?start={code}"' in issued and 'rel="noopener noreferrer"' in issued
