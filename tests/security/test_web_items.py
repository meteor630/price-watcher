"""Карточка товара на сайте: экранирование, без встроенного кода, ссылки в магазин, изменение количества и отметки, конфликт, CSRF, чужое — 404."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_web_home import EVIL, KEY, ORIGIN, TagScan, account_with_list, client, engine, no_inline_code, sign_in  # noqa: F401 — фикстуры модуля

from pricewatch.api.web_auth import CSRF_HEADER, SESSION_COOKIE
from pricewatch.application.web_auth import csrf_token_for


def item_of(engine: Engine, login: str) -> tuple[int, int, int, int]:
    with engine.connect() as db:
        return tuple(db.execute(
            text("SELECT i.id, i.version, l.store_id, i.category_id FROM items i JOIN users u ON u.id = i.user_id "
                 "JOIN item_links l ON l.item_id = i.id WHERE u.login = :l ORDER BY i.id LIMIT 1"), {"l": login}
        ).one())  # type: ignore[return-value]


def htmx(client: TestClient) -> dict[str, str]:
    return {"HX-Request": "true", "Origin": ORIGIN, CSRF_HEADER: csrf_token_for(KEY.encode(), client.cookies[SESSION_COOKIE])}


def change(client: TestClient, item: int, **fields: str):  # type: ignore[no-untyped-def]
    return client.post(f"/web/items/{item}/settings", data=fields, headers=htmx(client))


class Links(TagScan):
    def __init__(self) -> None:
        super().__init__()
        self.external: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        values = dict(attrs)
        if tag == "a" and values.get("target") == "_blank":
            self.external.append(values)


def test_item_page_escaped_with_safe_store_links(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    item, version, store, _ = item_of(engine, login)
    sign_in(client, login)

    html = client.get(f"/web/items/{item}").text

    assert EVIL not in html and "&lt;img" in html
    no_inline_code(html)
    scan = Links()
    scan.feed(html)
    assert scan.external and all(link["rel"] == "noopener noreferrer" for link in scan.external)
    assert f'hx-post="/web/items/{item}/settings"' in html and f'&#34;version&#34;: &#34;{version}&#34;' not in html
    assert f'"version": "{version}"' in html and 'class="bigchart"' in html and "История" in html


def test_quantity_change_returns_updated_regions(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    item, version, _, _ = item_of(engine, login)
    sign_in(client, login)

    response = change(client, item, version=str(version), quantity="3", view="item")

    assert response.status_code == 200
    assert 'id="hero-figures"' in response.text and 'id="page-rest"' in response.text and 'id="flash"' in response.text
    assert '<span class="stepper__value num">3</span>' in response.text
    assert item_of(engine, login)[1] == version + 1


def test_mark_change_from_category_page_rerenders_category(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=2)
    item, version, _, category = item_of(engine, login)
    sign_in(client, login)
    before = client.get(f"/web/categories/{category}").text

    response = change(client, item, version=str(version), in_total="false", view="category", period="week")

    assert response.status_code == 200 and "Позиции" in response.text and "за неделю" in response.text
    assert "1 в итоге, ещё 1 только наблюдаю" in response.text and "2 в итоге, ещё 0 только наблюдаю" in before


def test_conflict_shows_current_data_and_message(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    item, version, _, _ = item_of(engine, login)
    sign_in(client, login)
    change(client, item, version=str(version), quantity="2", view="item")

    response = change(client, item, version=str(version), quantity="5", view="item")

    assert response.status_code == 200 and "Запись уже изменили в другом месте" in response.text
    assert '<span class="stepper__value num">2</span>' in response.text


def test_change_needs_csrf(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    item, version, _, _ = item_of(engine, login)
    sign_in(client, login)

    response = client.post(f"/web/items/{item}/settings", data={"version": str(version), "quantity": "2"}, headers={"HX-Request": "true", "Origin": ORIGIN})

    assert response.status_code == 403 and item_of(engine, login)[1] == version


@pytest.mark.parametrize(
    "fields",
    [{"quantity": "2"}, {"version": "x", "quantity": "2"}, {"version": "1", "quantity": "0"}, {"version": "1", "quantity": "1000"},
     {"version": "1", "in_total": "maybe"}, {"version": "1", "quantity": "2", "view": "admin"}, {"version": "1"}],
)
def test_bad_fields_are_refused(client: TestClient, engine: Engine, fields: dict) -> None:
    login = account_with_list(engine, items=1)
    item, version, _, _ = item_of(engine, login)
    sign_in(client, login)

    response = change(client, item, **fields)

    assert response.status_code == 422 and 'data-error-code="VALIDATION_ERROR"' in response.text
    assert item_of(engine, login)[1] == version


def test_chart_fragment_switches_store_and_range(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    item, _, store, _ = item_of(engine, login)
    sign_in(client, login)

    response = client.get(f"/web/items/{item}/chart", params={"store_id": store, "range": "week"}, headers={"HX-Request": "true"})

    assert response.status_code == 200 and response.text.count('class="bigchart__hit"') == 7
    no_inline_code(response.text)


def test_someone_elses_item_is_not_found(client: TestClient, engine: Engine) -> None:
    item, version, store, _ = item_of(engine, account_with_list(engine, items=1))
    sign_in(client, account_with_list(engine, items=0))

    responses = [
        client.get(f"/web/items/{item}"),
        client.get(f"/web/items/{item}/chart", params={"store_id": store}),
        change(client, item, version=str(version), quantity="9", view="item"),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]


def test_category_rows_link_to_item_and_have_controls(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    item, _, _, category = item_of(engine, login)
    sign_in(client, login)

    html = client.get(f"/web/categories/{category}").text

    assert f'href="/web/items/{item}"' in html and 'class="check"' in html and 'class="stepper"' in html
