"""Решения по подозрительным изменениям у каждого свои (решение владельца 07.10.2026): общая ссылка, разные итоги."""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, World

from pricewatch.application.item import item_detail
from pricewatch.domain.pricing import PriceStatus
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
        db.execute(text("TRUNCATE users, stores, workers, settings CASCADE"))


def watch(world: World, link: int, store: int, name: str = "Монитор", since: datetime = NOW - 60 * DAY) -> int:
    """Товар пользователя на уже существующую (общую) ссылку."""
    item = world.item(world.category(f"Кат {name}"), name)
    world.one("INSERT INTO item_links (user_id, item_id, tracked_link_id, store_id, valid_from) VALUES (:u, :i, :l, :s, :f) RETURNING id",
              u=world.user.id, i=item, l=link, s=store, f=since)
    return item


def suspicious(world: World, link: int, at: datetime, price: int, title: str | None = None) -> int:
    world.seen(link, at, price=price, confirmation="pending")
    if title is not None:
        world.one("UPDATE price_observations SET title = :t WHERE tracked_link_id = :l AND checked_at = :at RETURNING id", t=title, l=link, at=at)
    return world.one("SELECT id FROM price_observations WHERE tracked_link_id = :l AND checked_at = :at", l=link, at=at)


def decide(world: World, observation: int, decision: str) -> None:
    world.one("INSERT INTO price_decisions (observation_id, user_id, decision) VALUES (:o, :u, :d) RETURNING id", o=observation, u=world.user.id, d=decision)


def total(world: World) -> int:
    return world.board().total_minor


def test_each_user_decides_for_own_items(engine: Engine) -> None:
    me, other = World(engine), World(engine)
    store = me.store("Ozon")
    mine = me.item(me.category("Компьютер"), "Монитор")
    link = me.link(mine, store)
    theirs = watch(other, link, store)
    me.seen(link, NOW - 3 * DAY, 30_000_00)
    jump = suspicious(me, link, NOW - 2 * DAY, 45_000_00)
    assert (total(me), total(other)) == (30_000_00, 30_000_00)
    assert item_detail(SqlUnitOfWork(engine), me.user, mine, NOW).under_review

    decide(me, jump, "accept")
    suspicious(me, link, NOW - DAY, 45_200_00)  # у меня — в пределах порога от принятой цены

    assert total(me) == 45_200_00
    assert total(other) == 30_000_00
    assert not item_detail(SqlUnitOfWork(engine), me.user, mine, NOW).under_review
    assert item_detail(SqlUnitOfWork(engine), other.user, theirs, NOW).under_review


def test_reject_keeps_own_price(engine: Engine) -> None:
    me, other = World(engine), World(engine)
    store = me.store()
    link = me.link(me.item(me.category("Дом"), "Лампа"), store)
    watch(other, link, store)
    me.seen(link, NOW - 3 * DAY, 1000_00)
    jump = suspicious(me, link, NOW - 2 * DAY, 2000_00)

    decide(other, jump, "reject")
    decide(me, jump, "accept")

    assert (total(me), total(other)) == (2000_00, 1000_00)


def test_new_watcher_sees_current_price_without_question(engine: Engine) -> None:
    first, newcomer = World(engine), World(engine)
    store = first.store()
    link = first.link(first.item(first.category("Дом"), "Лампа"), store)
    first.seen(link, NOW - 5 * DAY, 1000_00)
    suspicious(first, link, NOW - 4 * DAY, 2000_00)
    item = watch(newcomer, link, store, since=NOW - 2 * DAY)

    assert total(first) == 1000_00
    assert total(newcomer) == 2000_00
    assert not item_detail(SqlUnitOfWork(engine), newcomer.user, item, NOW).under_review


def test_accepted_title_long_ago_is_users_baseline(engine: Engine) -> None:
    me = World(engine)
    store = me.store()
    link = me.link(me.item(me.category("Дом"), "Монитор"), store)
    me.one("UPDATE tracked_links SET baseline_title = 'Монитор LG 27GR93U' WHERE id = :l RETURNING id", l=link)
    me.seen(link, NOW - 300 * DAY, 30_000_00)
    renamed = suspicious(me, link, NOW - 200 * DAY, 30_000_00, title="Холодильник Атлант")
    decide(me, renamed, "accept")
    me.seen(link, NOW - 100 * DAY, 30_000_00)  # подтверждённая для всех — окно загрузки начинается с неё
    suspicious(me, link, NOW - DAY, 30_500_00, title="Холодильник Атлант")

    assert total(me) == 30_500_00  # принятое давно название — эталон пользователя, хотя решение вне окна


def test_same_store_different_links_are_independent(engine: Engine) -> None:
    # Пример 3: у каждого своя ссылка одного магазина — свои проверки и решения.
    me, other = World(engine), World(engine)
    store = me.store("Ozon")
    my_link = me.link(me.item(me.category("Компьютер"), "Монитор"), store)
    their_item = other.item(other.category("Компьютер"), "Монитор")
    their_link = other.link(their_item, store)
    me.seen(my_link, NOW - 3 * DAY, 30_000_00)
    other.seen(their_link, NOW - 3 * DAY, 31_000_00)
    jump = suspicious(other, their_link, NOW - DAY, 50_000_00)
    decide(other, jump, "accept")

    assert (total(me), total(other)) == (30_000_00, 50_000_00)
    assert item_detail(SqlUnitOfWork(engine), other.user, their_item, NOW).price_status is PriceStatus.OK


def test_history_and_change_follow_users_view(engine: Engine) -> None:
    me = World(engine)
    store = me.store()
    item = me.item(me.category("Дом"), "Лампа")
    link = me.link(item, store)
    me.seen(link, NOW - 3 * DAY, 1000_00)
    jump = suspicious(me, link, NOW - timedelta(hours=2), 1600_00)

    assert me.board().change is not None and me.board().change.change_minor == 0  # нерешённый скачок в изменение не идёт
    decide(me, jump, "accept")

    assert me.board().change is not None and me.board().change.change_minor == 600_00
    assert "1 600\xa0₽" in item_detail(SqlUnitOfWork(engine), me.user, item, NOW).history[0].text
