"""Страницы категории и группы на сайте: по сессии, данные экранированы, без встроенного кода, период и диапазон фрагментами, чужое — 404."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_web_home import EVIL, account_with_list, client, engine, no_inline_code, sign_in, sql  # noqa: F401 — фикстуры модуля


def ids(engine: Engine, login: str) -> tuple[int, int]:
    """Категория пользователя и новая группа в ней с одним из его товаров."""
    with engine.begin() as db:
        user, category = db.execute(
            text("SELECT u.id, c.id FROM users u JOIN categories c ON c.user_id = u.id WHERE u.login = :l"), {"l": login}
        ).one()
        group = db.execute(
            text("INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, :n, 'g', 0) RETURNING id"),
            {"u": user, "c": category, "n": f"Группа {EVIL}"},
        ).scalar_one()
        db.execute(text("UPDATE items SET group_id = :g WHERE id = (SELECT min(id) FROM items WHERE user_id = :u)"), {"g": group, "u": user})
    return category, group


def test_category_page_escaped_without_inline_code(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, category=f"Кат {EVIL}")
    category, group = ids(engine, login)
    sign_in(client, login)

    html = client.get(f"/web/categories/{category}").text

    assert EVIL not in html and "&lt;img" in html
    no_inline_code(html)
    assert f'href="/web/groups/{group}"' in html and 'class="bigchart"' in html
    assert html.count('" data-item="') == 2 and "Итого" in html


def test_period_and_chart_range_fragments(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine)
    category, _ = ids(engine, login)
    sign_in(client, login)

    week = client.get(f"/web/categories/{category}", params={"period": "week"}, headers={"HX-Request": "true"})
    year = client.get(f"/web/categories/{category}/chart", params={"range": "year"}, headers={"HX-Request": "true"})

    assert week.status_code == 200 and 'id="page-rest"' in week.text and "за неделю" in week.text
    assert year.status_code == 200 and year.text.count('class="bigchart__hit"') == 365
    no_inline_code(year.text)


def test_group_page_shows_offers_and_minimum(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine)
    category, group = ids(engine, login)
    sign_in(client, login)

    html = client.get(f"/web/groups/{group}", params={"period": "week"}).text

    assert EVIL not in html and f'href="/web/categories/{category}"' in html
    assert html.count("<tr data-item=") == 1 and 'data-label="Минимум периода"' in html
    no_inline_code(html)


def test_someone_elses_pages_are_not_found(client: TestClient, engine: Engine) -> None:
    category, group = ids(engine, account_with_list(engine))
    sign_in(client, account_with_list(engine))

    for path in (f"/web/categories/{category}", f"/web/categories/{category}/chart", f"/web/groups/{group}"):
        response = client.get(path)
        assert response.status_code == 404 and 'data-error-code="NOT_FOUND"' in response.text


@pytest.mark.parametrize("path", ["/web/categories/0", "/web/categories/x", "/web/groups/1?period=decade", "/web/categories/1/chart?range=day"])
def test_bad_parameters_give_html_error(client: TestClient, engine: Engine, path: str) -> None:
    sign_in(client, account_with_list(engine, items=0))

    response = client.get(path)

    assert response.status_code == 422 and 'data-error-code="VALIDATION_ERROR"' in response.text


def test_pages_need_session(client: TestClient) -> None:
    for path in ("/web/categories/1", "/web/groups/1", "/web/categories/1/chart"):
        assert client.get(path, follow_redirects=False).headers["location"] == "/web/login"


def test_home_cards_link_to_categories(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine)
    category, _ = ids(engine, login)
    sign_in(client, login)

    assert f'href="/web/categories/{category}"' in client.get("/web/").text
