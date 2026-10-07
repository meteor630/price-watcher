"""Колокольчик администратора на настоящей базе: тексты, порядок, повторы снова непрочитаны, отметка своих, права."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import NOW, World

from pricewatch.application.notifications import bell_page, mark_read, report_unexpected
from pricewatch.domain.accounts import Role
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings, notifications CASCADE"))


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


def paused(engine: Engine, at: object, store: str = "Ozon") -> None:
    with uow(engine) as work:
        work.notifications.notify_admins(
            "store_paused", f"store_paused:{store}", {"store": store, "host": "ozon.ru", "reason": "CAPTCHA", "paused_until": "2026-10-07T06:00:00+00:00"}, at,
        )
        work.commit()


def test_texts_order_and_repeats(engine: Engine) -> None:
    admin, other_admin = World(engine, Role.ADMIN), World(engine, Role.ADMIN)
    paused(engine, NOW - timedelta(hours=2))
    report_unexpected(uow(engine), "E-ABCDEFGHJK", "GET", "/web/items/{item_id}", "RuntimeError", NOW - timedelta(hours=1))

    page = bell_page(uow(engine), admin.user, 0, 20)

    assert [item.kind for item in page.items] == ["unexpected_error", "store_paused"]
    assert page.items[1].text == "«Ozon» на паузе до 07.10 в 09:00: магазин запросил проверку «я не робот»."
    assert page.items[1].web_href == "/web/settings/stores"
    assert "RuntimeError в GET /web/items/{item_id}" in page.items[0].text and "E-ABCDEFGHJK" in page.items[0].text
    assert (page.unread_count, page.next_offset) == (2, None)

    assert mark_read(uow(engine), admin.user, None, NOW) == 0
    paused(engine, NOW)  # тот же инцидент повторился

    again = bell_page(uow(engine), admin.user, 0, 20)
    assert (again.items[0].kind, again.items[0].repeat_count, again.items[0].read, again.unread_count) == ("store_paused", 2, False, 1)
    assert bell_page(uow(engine), other_admin.user, 0, 20).unread_count == 2  # у каждого администратора — свои отметки


def test_mark_read_touches_only_own_and_pages(engine: Engine) -> None:
    admin, other = World(engine, Role.ADMIN), World(engine, Role.ADMIN)
    for index in range(3):
        paused(engine, NOW + timedelta(minutes=index), store=f"Магазин {index}")
    foreign = other.one("SELECT id FROM notifications WHERE user_id = :u AND channel = 'in_app' ORDER BY id LIMIT 1", u=other.user.id)
    first_page = bell_page(uow(engine), admin.user, 0, 2)

    left = mark_read(uow(engine), admin.user, [first_page.items[0].notification_id, foreign], NOW)

    assert (len(first_page.items), first_page.next_offset, left) == (2, 2, 2)
    assert bell_page(uow(engine), other.user, 0, 20).unread_count == 3


def test_only_admin(engine: Engine) -> None:
    user = World(engine)

    for action in (lambda: bell_page(uow(engine), user.user, 0, 20), lambda: mark_read(uow(engine), user.user, None, NOW)):
        with pytest.raises(PriceWatcherError) as caught:
            action()
        assert caught.value.code is ErrorCode.FORBIDDEN
