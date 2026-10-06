"""Настройки «Товары» на сайте: список и фильтр, окно товара, создание со ссылками, изменение, удаление с отменой, ссылки, порядок, CSRF, чужое — 404."""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_web_home import EVIL, KEY, ORIGIN, account_with_list, client, engine, no_inline_code, sign_in  # noqa: F401 — фикстуры модуля

from pricewatch.api.web_auth import CSRF_HEADER, SESSION_COOKIE
from pricewatch.application.web_auth import csrf_token_for

PAGE = "/web/settings/items"


def htmx(client: TestClient) -> dict[str, str]:
    return {"HX-Request": "true", "Origin": ORIGIN, CSRF_HEADER: csrf_token_for(KEY.encode(), client.cookies[SESSION_COOKIE])}


def post(client: TestClient, path: str, **fields: str):  # type: ignore[no-untyped-def]
    return client.post(path, data=fields, headers=htmx(client))


def one(engine: Engine, sql: str, **params: object):  # type: ignore[no-untyped-def]
    with engine.connect() as db:
        return db.execute(text(sql), params).one()


def user_items(engine: Engine, login: str) -> list[tuple]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(
            text("SELECT i.id, i.name, i.version, i.category_id, i.quantity FROM items i JOIN users u ON u.id = i.user_id "
                 "WHERE u.login = :l AND i.deleted_at IS NULL ORDER BY i.position, i.id"), {"l": login})]


def category_of(engine: Engine, login: str) -> int:
    return one(engine, "SELECT c.id FROM categories c JOIN users u ON u.id = c.user_id WHERE u.login = :l ORDER BY c.id LIMIT 1", l=login)[0]


def test_settings_root_goes_to_items(client: TestClient) -> None:
    assert client.get("/web/settings", follow_redirects=False).headers["location"] == PAGE


def test_page_lists_items_escaped_with_filter(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, category=f"Кат {EVIL}", items=2)
    sign_in(client, login)
    category = category_of(engine, login)

    html = client.get(PAGE).text
    filtered = client.get(PAGE, params={"category": category}).text

    assert EVIL not in html and "&lt;img" in html
    no_inline_code(html)
    assert html.count('class="edit-row"') == 2 and "Все <span class=\"num\">2</span>" in html
    assert f'aria-current="true">Кат' in filtered


def test_filter_by_foreign_category_is_not_found(client: TestClient, engine: Engine) -> None:
    foreign = category_of(engine, account_with_list(engine, items=0))
    sign_in(client, account_with_list(engine, items=0))

    assert client.get(PAGE, params={"category": foreign}).status_code == 404


def test_new_item_dialog(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)

    html = client.get("/web/settings/items/new").text

    assert "<dialog" in html and 'name="links"' in html and "data-open" in html
    no_inline_code(html)


def test_create_item_with_links_keeps_dialog_and_reports_bad_link(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    category = category_of(engine, login)
    good = f"https://www.shop-{uuid.uuid4().hex[:8]}.example/p/lamp"

    response = post(client, PAGE, name="Лампа", full_name="", quantity="2", category_id=str(category), group_id="", links=f"{good}\nне адрес")

    assert response.status_code == 200
    (item_id, name, _, _, quantity), = user_items(engine, login)
    assert (name, quantity) == ("Лампа", 2)
    assert 'id="dialog"' in response.text and "Изменить товар" in response.text and "не адрес" in response.text
    assert "проверяется…" in response.text and 'hx-trigger="every 3s"' in response.text
    assert one(engine, "SELECT count(*) FROM item_links WHERE item_id = :i AND valid_to IS NULL", i=item_id)[0] == 1


def test_edit_item_and_conflict(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    (item_id, _, version, category, _), = user_items(engine, login)

    dialog = client.get(f"/web/items/{item_id}/edit").text
    saved = post(client, f"/web/items/{item_id}/edit", version=str(version), name="Новое имя", full_name="Полное", quantity="4", category_id=str(category), group_id="")
    stale = post(client, f"/web/items/{item_id}/edit", version=str(version), name="Снова", full_name="", quantity="1", category_id=str(category), group_id="")

    assert "Изменить товар" in dialog and "Снять" in dialog and "data-close-on-success" in dialog
    no_inline_code(dialog)
    assert saved.status_code == 200 and user_items(engine, login)[0][1:5:3] == ("Новое имя", 4)
    assert "Запись уже изменили в другом месте" in stale.text


def test_delete_item_then_undo(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    (item_id, _, version, _, _), = user_items(engine, login)

    deleted = post(client, f"/web/items/{item_id}/delete", version=str(version))
    token = re.search(r'"token": "([^"]+)"', deleted.text)

    assert "Товар удалён." in deleted.text and token is not None and user_items(engine, login) == []
    assert post(client, "/web/undo", token=token.group(1)).status_code == 204
    assert len(user_items(engine, login)) == 1


def test_link_add_remove_and_undo(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    (item_id, _, _, _, _), = user_items(engine, login)

    added = post(client, f"/web/items/{item_id}/links", url=f"https://www.shop-{uuid.uuid4().hex[:8]}.example/p/x")
    links = [int(value) for value in re.findall(rf"/web/items/{item_id}/links/(\d+)/remove", added.text)]
    # Снимаем старую ссылку: часы теста стоят, а привязка нулевой длины в базе невозможна (и в жизни не бывает).
    removed = post(client, f"/web/items/{item_id}/links/{links[0]}/remove")
    token = re.search(r'"token": "([^"]+)"', removed.text)

    assert added.status_code == 200 and len(links) == 2 and 'id="item-links"' in added.text
    assert 'hx-swap-oob="true"' in removed.text and token is not None
    assert len(re.findall(r"/remove", removed.text)) == 1
    assert post(client, "/web/undo", token=token.group(1)).status_code == 204
    assert len(re.findall(r"/remove", client.get(f"/web/items/{item_id}/links").text)) == 2


def test_bad_link_is_explained_without_losing_others(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    (item_id, _, _, _, _), = user_items(engine, login)

    response = post(client, f"/web/items/{item_id}/links", url="javascript:alert(1)")

    assert response.status_code == 200 and "alert alert--error" in response.text
    assert len(re.findall(r"/remove", response.text)) == 1
    no_inline_code(response.text)


def test_order_and_toggle_keep_filter(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=2)
    sign_in(client, login)
    (first, _, _, category, _), (second, _, version2, _, _) = user_items(engine, login)
    category_version = one(engine, "SELECT version FROM categories WHERE id = :c", c=category)[0]

    ordered = post(client, "/web/items/order", category_id=str(category), group_id="", ids=f"{second},{first}", version=str(category_version), filter=str(category))
    toggled = post(client, f"/web/items/{second}/settings", version=str(version2), in_total="false", view="settings", filter=str(category))

    assert ordered.status_code == 200 and [row[0] for row in user_items(engine, login)] == [second, first]
    assert toggled.status_code == 200 and "наблюдаю" in toggled.text and f'aria-current="true">' in toggled.text


def test_group_options_follow_category(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    category = category_of(engine, login)
    post(client, f"/web/categories/{category}/groups", name="Свет")

    html = client.get("/web/settings/group-options", params={"category_id": category}).text

    assert 'name="group_id"' in html and "Свет" in html and "Без группы" in html


def test_edit_link_from_item_page_opens_dialog(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    (item_id, _, _, _, _), = user_items(engine, login)

    html = client.get(PAGE, params={"edit": item_id}).text

    assert "<dialog" in html and "data-open" in html and "Изменить товар" in html


@pytest.mark.parametrize("fields", [{"name": "x", "quantity": "0"}, {"name": "x", "quantity": "1", "category_id": "abc"}, {"name": "", "quantity": "1"}])
def test_bad_item_fields_are_refused(client: TestClient, engine: Engine, fields: dict) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    fields = {"category_id": str(category_of(engine, login)), "group_id": "", "full_name": ""} | fields

    response = post(client, PAGE, **fields)

    assert response.status_code == 422 and user_items(engine, login) == []


def test_changes_need_csrf_and_own_items(client: TestClient, engine: Engine) -> None:
    owner = account_with_list(engine, items=1)
    (item_id, _, version, category, _), = user_items(engine, owner)
    sign_in(client, account_with_list(engine, items=0))

    no_csrf = client.post(PAGE, data={"name": "x", "quantity": "1", "category_id": "1"}, headers={"HX-Request": "true", "Origin": ORIGIN})
    foreign = [
        client.get(f"/web/items/{item_id}/edit"),
        post(client, f"/web/items/{item_id}/edit", version=str(version), name="Взлом", full_name="", quantity="1", category_id=str(category), group_id=""),
        post(client, f"/web/items/{item_id}/delete", version=str(version)),
        post(client, f"/web/items/{item_id}/links", url="https://shop.example/p"),
        client.get(f"/web/items/{item_id}/links"),
    ]

    assert no_csrf.status_code == 403
    assert [response.status_code for response in foreign] == [404, 404, 404, 404, 404]
    assert user_items(engine, owner)[0][1] != "Взлом"
