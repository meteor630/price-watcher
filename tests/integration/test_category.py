"""Страницы категории и группы на настоящей базе: блоки по группам, цены строк, график, магазины группы, минимум периода."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, YESTERDAY, World

from pricewatch.application.category import category_chart, category_detail, group_detail
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
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


def group(world: World, category: int, name: str, position: int) -> int:
    return world.one(
        "INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, :n, lower(:n), :p) RETURNING id",
        u=world.user.id, c=category, n=name, p=position,
    )


def test_category_blocks_rows_and_totals(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Компьютер")
    power, case, empty = group(world, home, "Блок питания", 0), group(world, home, "Корпус", 1), group(world, home, "Пустая", 2)
    ozon, dns = world.store("Ozon", color="store-1"), world.store("DNS", color="store-2")
    loose = world.item(home, "Кабель", quantity=3)
    psu = world.item(home, "LIAN LI EDGE", group=power)
    box = world.item(home, "Lian Li O11", group=case, in_total=False)
    gone = world.item(home, "Вентилятор", group=case)
    world.seen(world.link(loose, ozon), NOW - timedelta(hours=1), 500_00)
    psu_link = world.link(psu, dns)
    world.seen(psu_link, YESTERDAY, 20_000_00)
    world.seen(psu_link, NOW - timedelta(hours=1), 19_000_00, card=18_299_00)
    world.seen(world.link(box, dns), NOW - timedelta(hours=1), 7_799_00)
    world.seen(world.link(gone, dns), NOW - timedelta(hours=1), availability="out_of_stock")
    world.seen(psu_link, NOW - timedelta(minutes=10), 1_000_00, confirmation="pending")

    detail = category_detail(SqlUnitOfWork(engine), world.user, home, Period.DAY, NOW)

    assert [(block.group_id, block.name) for block in detail.groups] == [(None, None), (power, "Блок питания"), (case, "Корпус"), (empty, "Пустая")]
    assert [block.total_minor for block in detail.groups] == [1_500_00, 18_299_00, 0, 0]
    assert detail.total_minor == 1_500_00 + 18_299_00 and (detail.in_total_count, detail.watching_count) == (3, 1)
    cable = detail.groups[0].items[0]
    assert (cable.quantity, cable.unit_price_text, cable.line_price_text, cable.regular_price_text) == (3, "500\xa0₽", "1 500\xa0₽", None)
    psu_row = detail.groups[1].items[0]
    assert (psu_row.unit_price_minor, psu_row.regular_price_text, psu_row.under_review) == (18_299_00, "19 000\xa0₽", True)
    assert psu_row.best_store is not None and (psu_row.best_store.name, psu_row.best_store.color_token) == ("DNS", "store-2")
    assert psu_row.change is not None and psu_row.change.percent_text == "8,5%"
    assert psu_row.checked_at == NOW - timedelta(hours=1)
    box_row, gone_row = detail.groups[2].items
    assert box_row.in_total is False and gone_row.price_status is PriceStatus.OUT_OF_STOCK and gone_row.unit_price_text is None
    assert detail.groups[3].items == []


def test_category_chart_ranges(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    world.seen(world.link(world.item(home, "Лампа"), world.store()), NOW - 3 * DAY, 1_000_00)

    week = category_chart(SqlUnitOfWork(engine), world.user, home, ChartRange.WEEK, NOW)
    year = category_chart(SqlUnitOfWork(engine), world.user, home, ChartRange.YEAR, NOW)

    assert len(week) == 7 and len(year) == 365
    assert week[-1].value_minor == 1_000_00 and week[0].value_minor is None
    assert week[-1].day == date(2026, 10, 6)


def test_empty_category_has_no_chart(engine: Engine) -> None:
    world = World(engine)

    assert category_chart(SqlUnitOfWork(engine), world.user, world.category("Пусто"), ChartRange.MONTH, NOW) == []


@pytest.mark.parametrize("deleted", [False, True])
def test_someone_elses_or_deleted_category_is_not_found(engine: Engine, deleted: bool) -> None:
    owner, stranger = World(engine), World(engine)
    category = owner.category("Дом", deleted=deleted)
    reader = owner if deleted else stranger

    for action in (
        lambda: category_detail(SqlUnitOfWork(engine), reader.user, category, Period.DAY, NOW),
        lambda: category_chart(SqlUnitOfWork(engine), reader.user, category, ChartRange.WEEK, NOW),
    ):
        with pytest.raises(PriceWatcherError) as caught:
            action()
        assert caught.value.code is ErrorCode.NOT_FOUND


def test_group_offers_and_period_minimum(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    lamps = group(world, home, "Лампы", 0)
    ozon, dns = world.store("Ozon", color="store-1"), world.store("DNS", color="store-2")
    lamp = world.item(home, "Лампа", group=lamps)
    ozon_link, dns_link = world.link(lamp, ozon), world.link(lamp, dns)
    world.seen(ozon_link, NOW - 5 * DAY, 900_00)  # минимум недели
    world.seen(ozon_link, NOW - timedelta(hours=2), 1_100_00)
    world.seen(dns_link, NOW - timedelta(hours=1), 1_000_00)
    world.item(home, "Вне группы")

    detail = group_detail(SqlUnitOfWork(engine), world.user, lamps, Period.WEEK, NOW)

    assert (detail.name, detail.category_name) == ("Лампы", "Дом") and len(detail.items) == 1
    item = detail.items[0]
    assert [(offer.store.name, offer.price_text, offer.best) for offer in item.offers] == [("Ozon", "1 100\xa0₽", False), ("DNS", "1 000\xa0₽", True)]
    assert item.minimum is not None and (item.minimum.price_text, item.minimum.store_name) == ("900\xa0₽", "Ozon")
    assert item.minimum.day == (NOW - 5 * DAY).date()
    no_low = group_detail(SqlUnitOfWork(engine), world.user, lamps, Period.DAY, NOW).items[0]
    assert no_low.minimum is not None and no_low.minimum.price_text == "1 000\xa0₽"


def test_someone_elses_group_is_not_found(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    lamps = group(owner, owner.category("Дом"), "Лампы", 0)

    with pytest.raises(PriceWatcherError) as caught:
        group_detail(SqlUnitOfWork(engine), stranger.user, lamps, Period.DAY, NOW)

    assert caught.value.code is ErrorCode.NOT_FOUND
