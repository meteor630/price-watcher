"""«Требует решения» на сайте: свои карточки, экранирование, принять с «Отменить», перепроверка, чужое — 404, CSRF."""

import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_web_home import EVIL, NOW, account_with_list, client, engine, no_inline_code, sign_in, sql  # noqa: F401 — фикстуры модуля
from test_web_settings_items import htmx


def pending_jump(engine: Engine, login: str, title: str = EVIL) -> int:
    """У первой ссылки пользователя — подозрительный скачок цены с названием `title`."""
    link = sql(engine, "SELECT l.tracked_link_id FROM item_links l JOIN users u ON u.id = l.user_id WHERE u.login = :l ORDER BY l.id LIMIT 1", l=login)
    run = sql(engine, "INSERT INTO parse_runs (kind, status, total, created_at, finished_at) VALUES ('on_add', 'finished', 0, :t, :t) RETURNING id", t=NOW)
    task = sql(engine, "INSERT INTO check_tasks (run_id, tracked_link_id, status, finished_at, created_at) VALUES (:r, :l, 'done', :t, :t) RETURNING id",
               r=run, l=link, t=NOW - timedelta(minutes=30))
    return sql(engine, "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, availability, region_code, confirmation, source, title) "
                       "VALUES (:l, :k, :t, 'ok', 300000, 'in_stock', 'spb', 'pending', 'site_data', :title) RETURNING id",
               l=link, k=task, t=NOW - timedelta(minutes=30), title=title)


def token_of(html: str) -> str:
    match = re.search(r"&#34;token&#34;: &#34;([^&]+)&#34;|\"token\": \"([^\"]+)\"", html)
    assert match is not None
    return match.group(1) or match.group(2)


def test_card_on_home_escaped_and_accept_with_undo(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    review = pending_jump(engine, login)
    sign_in(client, login)

    home = client.get("/web/").text
    assert f'data-review="{review}"' in home and "Скачок цены" in home and "150,0%" in home and "3 000\xa0₽" in home
    assert EVIL not in home
    no_inline_code(home)

    accepted = client.post(f"/web/reviews/{review}/accept", data={"period": "day"}, headers=htmx(client))
    assert accepted.status_code == 200 and "data-review" not in accepted.text and "Новое значение принято." in accepted.text
    assert sql(engine, "SELECT count(*) FROM price_decisions WHERE observation_id = :o", o=review) == 1
    assert client.post(f"/web/reviews/{review}/reject", data={"period": "day"}, headers=htmx(client)).status_code == 404  # уже решено

    undone = client.post("/web/undo", data={"token": token_of(accepted.text)}, headers=htmx(client))
    assert undone.status_code == 204 and sql(engine, "SELECT count(*) FROM price_decisions WHERE observation_id = :o", o=review) == 0
    assert f'data-review="{review}"' in client.get("/web/").text


def test_title_change_card_has_no_price_row_or_recheck(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    link = sql(engine, "SELECT l.tracked_link_id FROM item_links l JOIN users u ON u.id = l.user_id WHERE u.login = :l", l=login)
    sql(engine, "UPDATE tracked_links SET baseline_title = 'Монитор LG 27GR93U' WHERE id = :l RETURNING id", l=link)
    review = pending_jump(engine, login, title="Чехол для телефона")
    sql(engine, "UPDATE price_observations SET price_minor = 120000 WHERE id = :o RETURNING id", o=review)
    sign_in(client, login)

    home = client.get("/web/").text

    assert "Название изменилось" in home and "Чехол для телефона" in home
    assert "review-card__change" not in home and f"/web/reviews/{review}/recheck" not in home


def test_recheck_and_title_change(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    review = pending_jump(engine, login)
    sign_in(client, login)

    first = client.post(f"/web/reviews/{review}/recheck", data={"period": "day"}, headers=htmx(client))
    second = client.post(f"/web/reviews/{review}/recheck", data={"period": "week"}, headers=htmx(client))

    assert first.status_code == 200 and "Перепроверка поставлена" in first.text and "Отменить" in first.text
    assert second.status_code == 200 and "Эта ссылка уже проверяется." in second.text and "toast__undo" not in second.text


def test_someone_elses_review_and_csrf(client: TestClient, engine: Engine) -> None:
    review = pending_jump(engine, account_with_list(engine, items=1))
    sign_in(client, account_with_list(engine, items=1))

    assert "data-review" not in client.get("/web/").text
    for action in ("accept", "reject", "recheck"):
        assert client.post(f"/web/reviews/{review}/{action}", data={"period": "day"}, headers=htmx(client)).status_code == 404
    assert client.post(f"/web/reviews/{review}/accept", data={"period": "day"}, headers={"HX-Request": "true"}).status_code == 403
    assert client.post(f"/web/reviews/{review}/accept", data={"period": "century"}, headers=htmx(client)).status_code == 422
