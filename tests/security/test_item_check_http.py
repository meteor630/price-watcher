"""«Проверить цену» у товара: на сайте и в /api/v1.

Задание на каждую действующую ссылку с повышенным приоритетом, повтор без дубля, плановое задание в очереди обгоняется
(приоритет поднят), лимит частоты, событие в журнале; чужой товар — 404; сайт — только с сессией и CSRF, API — только
с токеном приложения и Idempotency-Key; пока проверка идёт — «Проверяю…» с опросом, после — событие для карточки."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_dashboard_http import NOW as API_NOW
from test_dashboard_http import bearer, run_sql, schema, user_with_list
from test_dashboard_http import client as api_client  # noqa: F401 — фикстура модуля
from test_web_home import ORIGIN, account_with_list, client, engine, sign_in  # noqa: F401 — фикстуры модуля

from pricewatch.api.web_auth import CSRF_HEADER, SESSION_COOKIE
from pricewatch.application.manual_checks import MANUAL_CHECKS_PER_HOUR
from pricewatch.application.web_auth import csrf_token_for
from test_web_home import KEY

CHECK_SCHEMA = schema("ItemCheck")


@pytest.fixture(autouse=True)
def quiet_queue(engine: Engine) -> Iterator[None]:
    with engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET status = 'cancelled', lease_until = NULL, worker_id = NULL, finished_at = now() WHERE status IN ('queued', 'leased')"))
        # Домашний узел не на связи: ни один worker не обращался к серверу (другие тесты могли оставить пульс).
        db.execute(text("UPDATE workers SET last_seen_at = NULL"))
    yield


def items_of(engine: Engine, user_id: int) -> list[tuple[int, int]]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(  # type: ignore[misc]
            text("SELECT l.item_id, l.tracked_link_id FROM item_links l WHERE l.user_id = :u ORDER BY l.item_id"), {"u": user_id}
        )]


def user_id_of(engine: Engine, login: str) -> int:
    with engine.connect() as db:
        return db.execute(text("SELECT id FROM users WHERE login = :l"), {"l": login}).scalar_one()


def active_tasks(engine: Engine, link: int) -> list[tuple[int, int]]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(  # type: ignore[misc]
            text("SELECT id, priority FROM check_tasks WHERE tracked_link_id = :l AND status IN ('queued', 'leased')"), {"l": link}
        )]


def htmx(client: TestClient) -> dict[str, str]:
    return {"HX-Request": "true", "Origin": ORIGIN, CSRF_HEADER: csrf_token_for(KEY.encode(), client.cookies[SESSION_COOKIE])}


def api_check(client: TestClient, headers: dict[str, str], item: int):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/items/{item}/check", headers=headers | {"Idempotency-Key": uuid.uuid4().hex})


# --- API: обычный путь, граница, отказ ---


def test_api_check_queues_link_with_high_priority_and_matches_contract(api_client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(api_client, engine, user)
    item, link = items_of(engine, user)[0]

    response = api_check(api_client, headers, item)

    assert response.status_code == 202, response.text
    body = response.json()
    CHECK_SCHEMA.validate(body)
    assert body["checking"] is True and body["queued_tasks"] == 1 and body["worker_online"] is False
    ((_, priority),) = active_tasks(engine, link)
    assert priority == 100
    state = api_client.get(f"/api/v1/items/{item}/check", headers=headers)
    CHECK_SCHEMA.validate(state.json())
    assert state.json()["checking"] is True


def test_api_second_press_does_not_duplicate_and_scheduled_task_is_overtaken(api_client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(api_client, engine, user)
    item, link = items_of(engine, user)[0]
    # Задание прогона с обычным приоритетом, уже стоящее в очереди (как у плановой проверки).
    run = run_sql(engine, "INSERT INTO parse_runs (kind, status, total, created_at) VALUES ('on_add', 'running', 1, :t) RETURNING id", t=API_NOW)
    scheduled = run_sql(engine, "INSERT INTO check_tasks (run_id, tracked_link_id, priority, created_at) VALUES (:r, :l, 0, :t) RETURNING id", r=run, l=link, t=API_NOW)

    first = api_check(api_client, headers, item).json()
    second = api_check(api_client, headers, item).json()

    assert first["queued_tasks"] == 0 and second["queued_tasks"] == 0
    assert active_tasks(engine, link) == [(scheduled, 100)]


def test_api_foreign_item_is_not_found_and_needs_token_and_key(api_client: TestClient, engine: Engine) -> None:
    owner = user_with_list(engine, items=1)
    stranger = bearer(api_client, engine, user_with_list(engine, items=1))
    item, link = items_of(engine, owner)[0]

    assert api_check(api_client, stranger, item).status_code == 404
    assert api_client.get(f"/api/v1/items/{item}/check", headers=stranger).status_code == 404
    assert api_client.post(f"/api/v1/items/{item}/check", headers={"Idempotency-Key": uuid.uuid4().hex}).status_code == 401
    assert api_client.post(f"/api/v1/items/{item}/check", headers=bearer(api_client, engine, owner)).status_code == 422
    assert active_tasks(engine, link) == []


def test_api_rate_limit_answers_429_with_retry_time(api_client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(api_client, engine, user)
    item, _ = items_of(engine, user)[0]
    for _ in range(MANUAL_CHECKS_PER_HOUR):
        run_sql(engine, "INSERT INTO audit_events (actor_id, action, entity_type, entity_id, created_at) VALUES (:u, 'price_recheck', 'item', :i, :t) RETURNING id",
                u=user, i=item, t=API_NOW)

    response = api_check(api_client, headers, item)

    assert response.status_code == 429
    assert response.json()["error"]["retry_after_seconds"] > 0 and int(response.headers["Retry-After"]) > 0


def test_api_item_without_links_is_validation_error(api_client: TestClient, engine: Engine) -> None:
    user = user_with_list(engine, items=1)
    headers = bearer(api_client, engine, user)
    item, _ = items_of(engine, user)[0]
    run_sql(engine, "UPDATE item_links SET valid_to = :t WHERE item_id = :i RETURNING id", t=API_NOW, i=item)

    response = api_check(api_client, headers, item)

    assert response.status_code == 422 and response.json()["error"]["details"]["fields"] == {"links": "no_active_links"}


# --- сайт ---


def test_web_check_shows_checking_polls_and_returns_to_button(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    item, link = items_of(engine, user_id_of(engine, login))[0]

    page = client.get(f"/web/items/{item}").text
    assert f'hx-post="/web/items/{item}/check"' in page and "Проверить цену" in page

    pressed = client.post(f"/web/items/{item}/check", headers=htmx(client))
    assert pressed.status_code == 200
    assert "Проверяю…" in pressed.text and 'hx-trigger="every 3s"' in pressed.text
    # Карточка опрашивает саму страницу: вместе с кнопкой обновляются цена и цены по магазинам.
    assert f'hx-get="/web/items/{item}"' in pressed.text and 'hx-select-oob="#hero-price,#page-rest"' in pressed.text
    assert 'id="hero-price"' in client.get(f"/web/items/{item}").text
    assert "Домашний узел сейчас не на связи" in pressed.text
    assert len(active_tasks(engine, link)) == 1

    with engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET status = 'cancelled', finished_at = now() WHERE tracked_link_id = :l AND status = 'queued'"), {"l": link})
    done = client.get(f"/web/items/{item}/check?compact=1")
    assert "Проверить цену" in done.text and "every 3s" not in done.text
    page = client.get(f"/web/items/{item}").text
    assert "every 3s" not in page and "Проверено" in page


def test_web_check_needs_csrf_and_owner(client: TestClient, engine: Engine) -> None:
    owner = account_with_list(engine, items=1)
    item, link = items_of(engine, user_id_of(engine, owner))[0]
    sign_in(client, account_with_list(engine, items=1))

    assert client.post(f"/web/items/{item}/check", headers={"HX-Request": "true", "Origin": ORIGIN}).status_code == 403
    assert client.post(f"/web/items/{item}/check", headers=htmx(client)).status_code == 404
    assert client.get(f"/web/items/{item}/check").status_code == 404
    assert active_tasks(engine, link) == []


def test_web_rate_limit_is_explained(client: TestClient, engine: Engine) -> None:
    login = account_with_list(engine, items=1)
    sign_in(client, login)
    user = user_id_of(engine, login)
    item, _ = items_of(engine, user)[0]
    with engine.begin() as db:
        for _ in range(MANUAL_CHECKS_PER_HOUR):
            db.execute(text("INSERT INTO audit_events (actor_id, action, entity_type, entity_id, created_at) VALUES (:u, 'price_recheck', 'item', :i, now())"),
                       {"u": user, "i": item})

    response = client.post(f"/web/items/{item}/check", headers=htmx(client))

    assert response.status_code == 200
    assert "Слишком много проверок подряд. Следующая — через" in response.text
