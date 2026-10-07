"""Настройки «Пользователи» на сайте: только администратор, блокировка закрывает вход сразу, отказы понятным текстом,
снятие прав, приглашение показывается один раз и удаляется выбором, CSRF."""

import re

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_web_home import EVIL, account_with_list, client, engine, no_inline_code, sign_in, sql  # noqa: F401 — фикстуры модуля
from test_web_settings_items import htmx

from pricewatch.api.web_auth import SESSION_COOKIE

PAGE = "/web/settings/users"


def make_admin(engine: Engine, login: str) -> int:
    return sql(engine, "UPDATE users SET role = 'admin' WHERE login = :l RETURNING id", l=login)


def user_id(engine: Engine, login: str) -> int:
    return sql(engine, "SELECT id FROM users WHERE login = :l", l=login)


def test_only_admin(client: TestClient, engine: Engine) -> None:
    target = user_id(engine, account_with_list(engine, items=0))
    sign_in(client, account_with_list(engine, items=0))

    assert client.get(PAGE).status_code == 403
    assert client.get(f"/web/users/{target}/block").status_code == 403
    assert client.post(f"/web/users/{target}/block", headers=htmx(client)).status_code == 403
    assert client.post("/web/invites", data={"role": "admin"}, headers=htmx(client)).status_code == 403
    assert sql(engine, "SELECT count(*) FROM users WHERE id = :u AND disabled_at IS NULL", u=target) == 1


def test_page_escaped_and_own_row_without_actions(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    me = make_admin(engine, login)
    sign_in(client, login)

    page = client.get(PAGE).text

    assert 'href="/web/settings/users" aria-current="page"' in page and "это вы" in page
    assert f"/web/users/{me}/block" not in page
    assert EVIL not in page and "&lt;img" in page
    no_inline_code(page)


def test_block_closes_session_at_once_and_unblock(client: TestClient, engine: Engine) -> None:
    target_login = account_with_list(engine, items=0)
    sign_in(client, target_login)
    target_cookie = client.cookies[SESSION_COOKIE]
    admin_login = account_with_list(engine, items=0)
    make_admin(engine, admin_login)
    sign_in(client, admin_login)
    target = user_id(engine, target_login)

    dialog = client.get(f"/web/users/{target}/block").text
    blocked = client.post(f"/web/users/{target}/block", headers=htmx(client))

    assert "<dialog" in dialog and "Вход на сайт и в приложение закроется сразу" in dialog
    no_inline_code(dialog)
    assert blocked.status_code == 200 and "вход закрыт" in blocked.text and "заблокирован" in blocked.text
    admin_cookie = client.cookies[SESSION_COOKIE]
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, target_cookie)
    assert client.get("/web/", follow_redirects=False).headers["location"] == "/web/login"
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, admin_cookie)
    unblocked = client.post(f"/web/users/{target}/unblock", headers=htmx(client))
    assert unblocked.status_code == 200 and "Пользователь разблокирован." in unblocked.text


def test_refusals_are_explained(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    me = make_admin(engine, login)
    plain = user_id(engine, account_with_list(engine, items=0))
    sign_in(client, login)

    myself = client.post(f"/web/users/{me}/block", headers=htmx(client))
    not_admin = client.post(f"/web/users/{plain}/demote", headers=htmx(client))

    assert myself.status_code == 200 and "С собой так нельзя" in myself.text
    assert not_admin.status_code == 200 and "Он уже не администратор." in not_admin.text
    assert client.get(f"/web/users/{plain}/promote").status_code == 404


def test_demote_other_admin(client: TestClient, engine: Engine) -> None:
    login, other = account_with_list(engine, items=0), account_with_list(engine, items=0)
    make_admin(engine, login)
    other_id = make_admin(engine, other)
    sign_in(client, login)

    response = client.post(f"/web/users/{other_id}/demote", headers=htmx(client))

    assert response.status_code == 200 and "Права администратора сняты." in response.text
    assert sql(engine, "SELECT role FROM users WHERE id = :u", u=other_id) == "user"


def test_invite_shown_once_and_deleted_by_selection(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    make_admin(engine, login)
    sign_in(client, login)

    created = client.post("/web/invites", data={"role": "admin"}, headers=htmx(client))
    link = re.search(r'value="(https://prices\.example\.ru/web/register\?invite=[^"]+)"', created.text)
    again = client.get(PAGE).text
    invite = sql(engine, "SELECT max(id) FROM invites")

    assert created.status_code == 200 and link is not None and 'data-copy="invite-link"' in created.text
    no_inline_code(created.text)
    assert "/web/register?invite=" not in again and f'name="invite_{invite}"' in again
    assert client.post("/web/invites", data={"role": "owner"}, headers=htmx(client)).status_code == 422
    assert client.post("/web/invites/delete", data={"evil": "on"}, headers=htmx(client)).status_code == 422
    assert client.post("/web/invites/delete", data={}, headers=htmx(client)).status_code == 422
    deleted = client.post("/web/invites/delete", data={f"invite_{invite}": "on"}, headers=htmx(client))
    assert deleted.status_code == 200 and "Удалено приглашений: 1." in deleted.text
    assert sql(engine, "SELECT count(*) FROM invites WHERE id = :i", i=invite) == 0
    assert client.post("/web/invites", data={"role": "user"}, headers={"HX-Request": "true"}).status_code == 403  # без CSRF
