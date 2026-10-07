"""Настройки «Проверка» на сайте: только администратор, ошибки у полей с сохранением введённого, конфликт версий, CSRF."""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_web_home import EVIL, account_with_list, client, engine, no_inline_code, sign_in, sql  # noqa: F401 — фикстуры модуля
from test_web_settings_items import htmx

PAGE = "/web/settings/check"


@pytest.fixture(autouse=True)
def default_settings(engine: Engine) -> Iterator[None]:
    # База общая на весь прогон: настройки проверки одного теста не должны менять итоги в других.
    reset = ("WITH d AS (DELETE FROM settings WHERE key IN ('check_times', 'stale_after_days', 'title_similarity_min_percent', "
             "'price_jump_max_percent') RETURNING 1) SELECT count(*) FROM d")
    sql(engine, reset)
    yield
    sql(engine, reset)


def admin(engine: Engine) -> str:
    login = account_with_list(engine, items=0)
    sql(engine, "UPDATE users SET role = 'admin' WHERE login = :l RETURNING id", l=login)
    return login


def form(**changes: str) -> dict[str, str]:
    versions = {f"version_{key}": "0" for key in ("check_times", "stale_after_days", "title_similarity_min_percent", "price_jump_max_percent")}
    numbers = {"stale_after_days": "7", "title_similarity_percent": "85", "price_jump_percent": "30"}
    return {"check_time_1": "09:00", "check_time_2": "", "check_time_3": ""} | numbers | versions | changes


def test_only_admin_sees_and_saves(client: TestClient, engine: Engine) -> None:
    sign_in(client, account_with_list(engine, items=0))
    assert "/web/settings/check" not in client.get("/web/settings/items").text
    assert client.get(PAGE).status_code == 403
    assert client.post(PAGE, data=form(), headers=htmx(client)).status_code == 403

    sign_in(client, admin(engine))
    page = client.get(PAGE)

    assert page.status_code == 200 and 'href="/web/settings/check" aria-current="page"' in page.text
    assert 'value="09:00"' in page.text and 'value="7"' in page.text
    no_inline_code(page.text)
    assert "/web/settings/check" in client.get("/web/settings/items").text


def test_save_and_conflict(client: TestClient, engine: Engine) -> None:
    sign_in(client, admin(engine))

    saved = client.post(PAGE, data=form(check_time_1="21:30", check_time_3="08:00", stale_after_days="3"), headers=htmx(client))
    stale = client.post(PAGE, data=form(check_time_1="10:00"), headers=htmx(client))

    assert saved.status_code == 200 and "data-saved" in saved.text
    assert re.search(r'name="check_time_1" type="time" value="08:00"', saved.text) and 'value="21:30"' in saved.text
    assert sql(engine, "SELECT value_json::text FROM settings WHERE key = 'check_times'") == '["08:00", "21:30"]'
    assert stale.status_code == 200 and "Запись уже изменили" in stale.text and "data-saved" not in stale.text
    assert sql(engine, "SELECT value_json::text FROM settings WHERE key = 'stale_after_days'") == "3"


def test_field_errors_keep_entered_values_escaped(client: TestClient, engine: Engine) -> None:
    sign_in(client, admin(engine))

    response = client.post(PAGE, data=form(check_time_1="", check_time_2=EVIL, stale_after_days="0", price_jump_percent="500"), headers=htmx(client))

    assert response.status_code == 200
    assert response.text.count('aria-invalid="true"') == 4 and "Целое число от 5 до 200." in response.text
    assert "Укажите время ежедневной проверки." in response.text and "Время — ЧЧ:ММ" in response.text and "Целое число от 1 до 30." in response.text
    assert EVIL not in response.text and "&lt;img" in response.text
    no_inline_code(response.text)
    assert sql(engine, "SELECT count(*) FROM settings WHERE key IN ('check_times', 'stale_after_days', 'title_similarity_min_percent', 'price_jump_max_percent')") == 0


def test_forged_version_csrf_and_session(client: TestClient, engine: Engine) -> None:
    sign_in(client, admin(engine))

    assert client.post(PAGE, data=form(version_check_times="x"), headers=htmx(client)).status_code == 422
    assert client.post(PAGE, data=form(), headers={"HX-Request": "true"}).status_code == 403
    client.cookies.clear()
    assert client.get(PAGE, follow_redirects=False).headers["location"] == "/web/login"
