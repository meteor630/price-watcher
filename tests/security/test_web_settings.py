"""Настройки «Категории» на сайте: по сессии, экранирование, без встроенного кода, изменения с CSRF, удаление с отменой, порядок, группы, чужое — 404."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_web_home import EVIL, KEY, ORIGIN, account_with_list, client, engine, no_inline_code, sign_in  # noqa: F401 — фикстуры модуля

from pricewatch.api.web_auth import CSRF_HEADER, SESSION_COOKIE
from pricewatch.application.web_auth import csrf_token_for

PAGE = "/web/settings/categories"


def htmx(client: TestClient) -> dict[str, str]:
    return {"HX-Request": "true", "Origin": ORIGIN, CSRF_HEADER: csrf_token_for(KEY.encode(), client.cookies[SESSION_COOKIE])}


def post(client: TestClient, path: str, **fields: str):  # type: ignore[no-untyped-def]
    return client.post(path, data=fields, headers=htmx(client))


def categories(engine: Engine, login: str) -> list[tuple[int, str, int]]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(
            text("SELECT c.id, c.name, c.version FROM categories c JOIN users u ON u.id = c.user_id WHERE u.login = :l AND c.deleted_at IS NULL ORDER BY c.position"),
            {"l": login},
        )]


def order_version(engine: Engine, login: str) -> int:
    with engine.connect() as db:
        return db.execute(text("SELECT categories_order_version FROM users WHERE login = :l"), {"l": login}).scalar_one()


def test_page_needs_session_and_root_redirects(client: TestClient) -> None:
    assert client.get(PAGE, follow_redirects=False).headers["location"] == "/web/login"
    # Первая вкладка настроек — «Товары» (SITE_DESIGN_SPEC.md, «Навигация по вкладкам»).
    assert client.get("/web/settings", follow_redirects=False).headers["location"] == "/web/settings/items"


def test_page_is_escaped_without_inline_code(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, category=f"Кат {EVIL}")
    sign_in(client, login)

    html = client.get(PAGE).text

    assert EVIL not in html and "&lt;img" in html
    no_inline_code(html)
    assert 'data-sortable="order-categories"' in html and 'href="/web/settings"' in html


def test_create_and_name_taken(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, category="Дом", items=0)
    sign_in(client, login)

    created = post(client, PAGE, name="Кухня")
    taken = post(client, PAGE, name="дом")

    assert created.status_code == 200 and "Кухня" in created.text
    assert [name for _, name, _ in categories(engine, login)] == ["Дом", "Кухня"]
    assert taken.status_code == 409 and 'data-error-code="NAME_TAKEN"' in taken.text


def test_rename_inline(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    (category, _, version), = categories(engine, login)

    form = client.get(f"/web/categories/{category}/rename").text
    response = post(client, f"/web/categories/{category}/rename", name="Дача", version=str(version))
    stale = post(client, f"/web/categories/{category}/rename", name="Снова", version=str(version))

    assert 'name="name"' in form and "data-autofocus" in form
    assert response.status_code == 200 and categories(engine, login)[0][1] == "Дача"
    assert "Запись уже изменили в другом месте" in stale.text and categories(engine, login)[0][1] == "Дача"


def test_delete_empty_then_undo(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    (category, _, version), = categories(engine, login)

    deleted = post(client, f"/web/categories/{category}/delete", version=str(version))
    token = re.search(r'"token": "([^"]+)"', deleted.text)

    assert deleted.status_code == 200 and "Категория удалена." in deleted.text and token is not None
    assert categories(engine, login) == []
    undone = post(client, "/web/undo", token=token.group(1))
    assert undone.status_code == 204 and undone.headers["HX-Refresh"] == "true"
    assert len(categories(engine, login)) == 1


def test_delete_non_empty_moves_items(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, category="Старая", items=2)
    sign_in(client, login)
    post(client, PAGE, name="Новая")
    (old, _, version), (new, _, _) = categories(engine, login)

    dialog = client.get(f"/web/categories/{old}/delete").text
    response = post(client, f"/web/categories/{old}/delete", version=str(version), choice="move", move_to=str(new))

    assert "<dialog" in dialog and "Перенести в категорию" in dialog
    no_inline_code(dialog)
    assert response.status_code == 200 and [name for _, name, _ in categories(engine, login)] == ["Новая"]
    assert "2 товара" in response.text


@pytest.mark.parametrize("fields", [{"version": "1", "choice": "move"}, {"version": "1", "choice": "move", "move_to": "x"}, {"version": "1", "choice": "both"}, {"choice": "delete"}])
def test_bad_delete_is_refused(client: TestClient, engine: Engine, fields: dict) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    (category, _, _), = categories(engine, login)

    response = post(client, f"/web/categories/{category}/delete", **fields)

    assert response.status_code == 422 and len(categories(engine, login)) == 1


def test_reorder_from_settings_and_home(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, category="А", items=0)
    sign_in(client, login)
    post(client, PAGE, name="Б")
    (a, _, _), (b, _, _) = categories(engine, login)

    settings = post(client, "/web/categories/order", ids=f"{b},{a}", version=str(order_version(engine, login)), view="settings")
    home = post(client, "/web/categories/order", ids=f"{a},{b}", version=str(order_version(engine, login)), view="home", period="week")
    stale = post(client, "/web/categories/order", ids=f"{b},{a}", version="1", view="settings")

    assert settings.status_code == 200 and 'id="page-rest"' in settings.text
    assert home.status_code == 200 and "за неделю" in home.text and 'data-sortable="order-home"' in home.text
    assert [name for _, name, _ in categories(engine, login)] == ["А", "Б"]
    assert "Запись уже изменили в другом месте" in stale.text


@pytest.mark.parametrize("ids", ["", "1,1", "a,b", "1;2"])
def test_bad_order_is_refused(client: TestClient, engine: Engine, ids: str) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)

    response = post(client, "/web/categories/order", ids=ids, version=str(order_version(engine, login)))

    assert response.status_code == 422


def test_groups_create_rename_delete_and_order(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)
    (category, _, _), = categories(engine, login)

    post(client, f"/web/categories/{category}/groups", name="Первая")
    page = post(client, f"/web/categories/{category}/groups", name="Вторая").text
    with engine.connect() as db:
        groups = [tuple(row) for row in db.execute(text("SELECT id, version FROM groups WHERE category_id = :c ORDER BY position"), {"c": category})]
        category_version = db.execute(text("SELECT version FROM categories WHERE id = :c"), {"c": category}).scalar_one()
    (first, first_version), (second, second_version) = groups

    assert "Группы (2)" in page
    assert post(client, f"/web/groups/{first}/rename", name="Основная", version=str(first_version)).status_code == 200
    assert post(client, f"/web/categories/{category}/groups/order", ids=f"{second},{first}", version=str(category_version)).status_code == 200
    deleted = post(client, f"/web/groups/{second}/delete", version=str(second_version), items="keep")
    assert "Группа удалена." in deleted.text and "Группы (1)" in deleted.text and "Основная" in deleted.text
    assert post(client, f"/web/groups/{first}/delete", version="1", items="maybe").status_code == 422


def test_changes_need_csrf(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=0)
    sign_in(client, login)

    response = client.post(PAGE, data={"name": "Без токена"}, headers={"HX-Request": "true", "Origin": ORIGIN})

    assert response.status_code == 403 and len(categories(engine, login)) == 1


def test_someone_elses_records_are_not_found(client: TestClient, engine: Engine) -> None:
    owner = account_with_list(engine, items=0)
    (category, _, version), = categories(engine, owner)
    sign_in(client, account_with_list(engine, items=0))

    responses = [
        client.get(f"/web/categories/{category}/rename"),
        client.get(f"/web/categories/{category}/delete"),
        post(client, f"/web/categories/{category}/rename", name="Взлом", version=str(version)),
        post(client, f"/web/categories/{category}/delete", version=str(version)),
        post(client, f"/web/categories/{category}/groups", name="Чужая"),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404, 404, 404]
    assert categories(engine, owner)[0][1] != "Взлом"
