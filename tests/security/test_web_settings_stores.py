"""Настройки «Магазины» на сайте: только администратор, экранирование, правка в строке, отключение и удаление с подтверждением, CSRF."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_web_home import EVIL, account_with_list, client, engine, no_inline_code, sign_in, sql  # noqa: F401 — фикстуры модуля
from test_web_settings_items import htmx

PAGE = "/web/settings/stores"


def admin(engine: Engine) -> str:
    login = account_with_list(engine, items=1)
    sql(engine, "UPDATE users SET role = 'admin' WHERE login = :l RETURNING id", l=login)
    return login


def store_of(engine: Engine, login: str) -> int:
    return sql(engine, "SELECT l.store_id FROM item_links l JOIN users u ON u.id = l.user_id WHERE u.login = :l LIMIT 1", l=login)


def bare_store(engine: Engine) -> int:
    return sql(engine, "INSERT INTO stores (canonical_host, display_name, color, parser_key, auto_created, min_delay_ms) "
                       "VALUES (:h, 'Без ссылок', 'store-2', 'universal', true, 2000) RETURNING id", h=f"{uuid.uuid4().hex[:8]}.example")


def test_only_admin(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    store = store_of(engine, login)
    sign_in(client, login)

    assert PAGE not in client.get("/web/settings/items").text
    assert client.get(PAGE).status_code == 403
    assert client.get(f"/web/stores/{store}/edit").status_code == 403
    assert client.post(f"/web/stores/{store}/toggle", data={"enabled": "false"}, headers=htmx(client)).status_code == 403
    assert sql(engine, "SELECT enabled::int FROM stores WHERE id = :s", s=store) == 1


def test_page_escaped_and_edit_in_row(client: TestClient, engine: Engine) -> None:
    login = admin(engine)
    store = store_of(engine, login)
    sign_in(client, login)

    page = client.get(PAGE).text
    form = client.get(f"/web/stores/{store}/edit").text
    saved = client.post(f"/web/stores/{store}/edit", data={"name": "Озон", "color": "store-3"}, headers=htmx(client))

    assert f'data-store="{store}"' in page and EVIL not in page and "&lt;img" in page
    no_inline_code(page)
    no_inline_code(form)
    assert 'value="store-5" checked' in form
    assert saved.status_code == 200 and "Озон" in saved.text
    assert sql(engine, "SELECT color FROM stores WHERE id = :s", s=store) == "store-3"
    assert client.post(f"/web/stores/{store}/edit", data={"name": "Озон", "color": "red"}, headers=htmx(client)).status_code == 422


def test_toggle_and_delete_need_confirmation(client: TestClient, engine: Engine) -> None:
    login = admin(engine)
    used, unused = store_of(engine, login), bare_store(engine)
    sign_in(client, login)

    dialog = client.get(f"/web/stores/{used}/toggle").text
    off = client.post(f"/web/stores/{used}/toggle", data={"enabled": "false"}, headers=htmx(client))

    assert "<dialog" in dialog and "перестанут проверяться" in dialog
    no_inline_code(dialog)
    assert off.status_code == 200 and "отключён" in off.text and sql(engine, "SELECT enabled::int FROM stores WHERE id = :s", s=used) == 0
    assert client.post(f"/web/stores/{used}/toggle", data={"enabled": "maybe"}, headers=htmx(client)).status_code == 422
    client.post(f"/web/stores/{used}/toggle", data={"enabled": "true"}, headers=htmx(client))
    assert client.get(f"/web/stores/{used}/delete").status_code == 422
    assert client.post(f"/web/stores/{used}/delete", headers=htmx(client)).status_code == 422
    assert "<dialog" in client.get(f"/web/stores/{unused}/delete").text
    assert client.post(f"/web/stores/{unused}/delete", headers=htmx(client)).status_code == 200
    assert sql(engine, "SELECT count(*) FROM stores WHERE id = :s", s=unused) == 0
    assert client.post(f"/web/stores/{unused}/delete", headers=htmx(client)).status_code == 404


def test_csrf_and_session(client: TestClient, engine: Engine) -> None:
    login = admin(engine)
    store = store_of(engine, login)
    sign_in(client, login)

    assert client.post(f"/web/stores/{store}/toggle", data={"enabled": "false"}, headers={"HX-Request": "true"}).status_code == 403
    client.cookies.clear()
    assert client.get(PAGE, follow_redirects=False).headers["location"] == "/web/login"
