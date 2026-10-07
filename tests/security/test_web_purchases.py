"""«Куплено», «В архив», «Вернуть в список» на сайте: окна, переходы с «Отменить», блок «Куплено» в категории, ошибки формы, CSRF, чужое — 404."""

import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_web_home import EVIL, NOW, account_with_list, client, engine, no_inline_code, sign_in  # noqa: F401 — фикстуры модуля
from test_web_settings_items import category_of, htmx, one, post, user_items

from pricewatch.domain.days import moscow_date

TODAY = moscow_date(NOW).isoformat()


def first_item(engine: Engine, login: str) -> tuple[int, int]:
    item_id, _, version, _, _ = user_items(engine, login)[0]
    return item_id, version


def state(engine: Engine, item: int) -> tuple:
    return tuple(one(engine, "SELECT state, purchase_price_minor, purchase_quantity, purchase_store_id, archived_at FROM items WHERE id = :i", i=item))


def store_of(engine: Engine, item: int) -> int:
    return one(engine, "SELECT store_id FROM item_links WHERE item_id = :i", i=item)[0]


def undo_token(html: str) -> str:
    match = re.search(r"&#34;token&#34;: &#34;([^&]+)&#34;|\"token\": \"([^\"]+)\"", html)
    assert match is not None
    return match.group(1) or match.group(2)


def test_purchase_dialog_prefills_best_price_and_is_escaped(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    item, _ = first_item(engine, login)

    html = client.get(f"/web/items/{item}/purchase").text

    assert "<dialog" in html and 'value="1200"' in html and f'max="{TODAY}"' in html
    assert f'<option value="{store_of(engine, item)}" selected>' in html
    assert EVIL not in html and "&lt;img" in html
    no_inline_code(html)


def test_purchase_moves_item_out_of_list_and_undo_brings_it_back(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=2)
    sign_in(client, login)
    item, version = first_item(engine, login)
    category = category_of(engine, login)

    response = post(client, f"/web/items/{item}/purchase", version=str(version), price="12 990,50", quantity="2",
                    store_id=str(store_of(engine, item)), day=TODAY)

    assert response.status_code == 200
    assert state(engine, item)[:4] == ("purchased", 1_299_050, 2, store_of(engine, item))
    assert "data-purchase" in response.text and "Отмечено как купленное." in response.text and "Отменить" in response.text
    no_inline_code(response.text)
    page = client.get(f"/web/categories/{category}").text
    assert "Куплено (1)" in page and "25 981\xa0₽" in page
    no_inline_code(page)

    undone = client.post("/web/undo", data={"token": undo_token(response.text)}, headers=htmx(client))

    assert undone.status_code == 204 and undone.headers["HX-Refresh"] == "true"
    assert state(engine, item) == ("active", None, None, None, None)
    assert "Куплено (" not in client.get(f"/web/categories/{category}").text


def test_purchase_form_errors_keep_item_in_list(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    item, version = first_item(engine, login)
    good = {"version": str(version), "price": "100", "quantity": "1", "store_id": "", "day": TODAY}
    tomorrow = (moscow_date(NOW) + timedelta(days=1)).isoformat()

    for change in ({"price": "сто"}, {"price": "0"}, {"price": "-5"}, {"quantity": "0"}, {"quantity": "x"}, {"day": "06.10.2026"},
                   {"day": "2026-02-30"}, {"day": tomorrow}, {"day": "1999-12-31"}, {"store_id": "abc"}, {"version": "0"}):
        response = post(client, f"/web/items/{item}/purchase", **(good | change))
        assert response.status_code == 422, change
        assert 'data-error-code="VALIDATION_ERROR"' in response.text, change

    assert post(client, f"/web/items/{item}/purchase", **(good | {"store_id": "999999999"})).status_code == 422
    assert state(engine, item)[0] == "active"
    conflict = post(client, f"/web/items/{item}/purchase", **(good | {"version": str(version + 5)}))
    assert conflict.status_code == 200 and "Запись уже изменили" in conflict.text and state(engine, item)[0] == "active"


def test_archive_and_return_to_list(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    item, version = first_item(engine, login)

    archived = post(client, f"/web/items/{item}/archive", version=str(version))
    assert archived.status_code == 200 and "data-archived" in archived.text and "Товар перенесён в архив." in archived.text
    assert state(engine, item)[0] == "archived" and state(engine, item)[4] == NOW
    assert client.get(f"/web/items/{item}/return").status_code == 422  # подтверждение — только для купленного

    returned = post(client, f"/web/items/{item}/return", version=str(version + 1))

    assert returned.status_code == 200 and "Куплено</button>" in returned.text
    assert state(engine, item) == ("active", None, None, None, None)


def test_return_purchase_asks_first(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    item, version = first_item(engine, login)
    post(client, f"/web/items/{item}/purchase", version=str(version), price="500", quantity="1", store_id="", day=TODAY)

    dialog = client.get(f"/web/items/{item}/return").text

    assert "<dialog" in dialog and "будет удалена" in dialog and "500\xa0₽" in dialog
    no_inline_code(dialog)
    assert client.get(f"/web/items/{item}/purchase").status_code == 422
    assert post(client, f"/web/items/{item}/archive", version=str(version + 1)).status_code == 422
    assert post(client, f"/web/items/{item}/return", version=str(version + 1)).status_code == 200
    assert state(engine, item)[0] == "active"


def test_someone_elses_item_and_csrf(client: TestClient, engine: Engine) -> None:
    owner = account_with_list(engine, items=1)
    item, version = first_item(engine, owner)
    sign_in(client, account_with_list(engine, items=0))

    assert client.get(f"/web/items/{item}/purchase").status_code == 404
    assert client.get(f"/web/items/{item}/return").status_code == 404
    for path, fields in ((f"/web/items/{item}/purchase", {"price": "1", "quantity": "1", "store_id": "", "day": TODAY}),
                         (f"/web/items/{item}/archive", {}), (f"/web/items/{item}/return", {})):
        assert post(client, path, version=str(version), **fields).status_code == 404, path
        no_csrf = client.post(path, data={"version": str(version)} | fields, headers={"HX-Request": "true"})
        assert no_csrf.status_code == 403, path
    assert state(engine, item)[0] == "active"


def test_purchases_page_period_switch_and_escaping(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, category=f"Кат {EVIL}", items=2)
    sign_in(client, login)
    item, version = first_item(engine, login)
    post(client, f"/web/items/{item}/purchase", version=str(version), price="1 000", quantity="3", store_id=str(store_of(engine, item)), day=TODAY)

    month = client.get("/web/purchases")
    day = client.get("/web/purchases", params={"period": "day"}, headers={"HX-Request": "true"})

    assert month.status_code == 200 and day.status_code == 200
    assert "3 000\xa0₽" in month.text and 'data-spent' in month.text and "data-purchases-link" in month.text
    assert 'data-segmented data-active="2"' in month.text and 'data-segmented data-active="0"' in day.text  # месяц и сутки
    assert "1 покупка" in month.text and "сэкономлено" in month.text  # цена при добавлении 1200 ₽ > 1000 ₽
    assert EVIL not in month.text and "&lt;img" in month.text
    no_inline_code(month.text)
    assert client.get("/web/purchases", params={"period": "century"}).status_code == 422


def test_empty_purchases_and_archive(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=0))

    assert "Покупок пока нет" in client.get("/web/purchases").text
    archive = client.get("/web/purchases/archive").text
    assert "В архиве пусто" in archive
    no_inline_code(archive)


def test_archive_page_returns_item_to_list(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=2)
    sign_in(client, login)
    item, version = first_item(engine, login)
    post(client, f"/web/items/{item}/archive", version=str(version))

    page = client.get("/web/purchases/archive").text
    assert f'data-item="{item}"' in page and "1 200\xa0₽" in page and EVIL not in page
    no_inline_code(page)

    assert post(client, f"/web/items/{item}/return", version=str(version + 1), view="nowhere").status_code == 422
    stale = post(client, f"/web/items/{item}/return", version=str(version), view="archive")
    assert stale.status_code == 200 and "Запись уже изменили" in stale.text
    back = post(client, f"/web/items/{item}/return", version=str(version + 1), view="archive")

    assert back.status_code == 200 and "В архиве пусто" in back.text
    assert state(engine, item)[0] == "active"


def test_purchase_pages_need_session(client: TestClient) -> None:
    for path in ("/web/purchases", "/web/purchases/archive"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/web/login"
