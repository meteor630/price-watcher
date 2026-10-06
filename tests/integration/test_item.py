"""Карточка товара на настоящей базе: магазины, история, график, изменения с версией, устаревшая цена в итоге."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, YESTERDAY, World

from pricewatch.application.dashboard import dashboard
from pricewatch.application.item import item_chart, item_detail, update_item_settings
from pricewatch.application.worker_protocol import WorkerResult, claim_task, create_worker, submit_result
from pricewatch.domain.charts import ChartRange
from pricewatch.domain.days import Period
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.history import EventKind
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


def version(engine: Engine, item: int) -> int:
    with engine.connect() as db:
        return db.execute(text("SELECT version FROM items WHERE id = :i"), {"i": item}).scalar_one()


def test_detail_offers_history_failure_and_photo(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    lamps = world.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, 'Лампы', 'лампы', 0) RETURNING id", u=world.user.id, c=home)
    ozon, dns, old = world.store("Ozon", color="store-1"), world.store("DNS", color="store-2"), world.store("Ушедший", color="store-7")
    lamp = world.item(home, "Лампа", quantity=2, group=lamps)
    world.one("UPDATE items SET full_name = 'Лампа настольная LED' WHERE id = :i RETURNING id", i=lamp)
    replaced = world.link(lamp, old, until=NOW - 5 * DAY)
    world.seen(replaced, NOW - 20 * DAY, 1500_00)
    world.seen(replaced, NOW - 10 * DAY, 1400_00)
    ozon_link, dns_link = world.link(lamp, ozon), world.link(lamp, dns)
    world.seen(ozon_link, NOW - 3 * DAY, 1000_00)
    world.seen(ozon_link, NOW - 2 * DAY, availability="out_of_stock")
    world.seen(ozon_link, YESTERDAY, 950_00, card=900_00)
    world.seen(dns_link, YESTERDAY, 990_00)
    world.seen(dns_link, NOW - timedelta(hours=1), status="CAPTCHA")
    world.one("UPDATE tracked_links SET image_path = 'ab/' || repeat('a', 64) || '.webp' WHERE id = :l RETURNING id", l=dns_link)

    detail = item_detail(SqlUnitOfWork(engine), world.user, lamp, NOW)

    assert (detail.name, detail.full_name, detail.category_name, detail.group_name, detail.quantity) == ("Лампа", "Лампа настольная LED", "Дом", "Лампы", 2)
    assert (detail.unit_price_text, detail.regular_price_text, detail.line_price_text) == ("900\xa0₽", "950\xa0₽", "1 800\xa0₽")
    assert detail.best_store is not None and detail.best_store.name == "Ozon" and detail.failure_reason is None
    assert [(offer.store.name, offer.is_best, offer.price_status) for offer in detail.offers] == [("Ozon", True, PriceStatus.OK), ("DNS", False, PriceStatus.CHECK_FAILED)]  # прежняя цена показана с причиной
    dns_offer = detail.offers[1]
    assert dns_offer.price_text == "990\xa0₽" and dns_offer.checked_at == NOW - timedelta(hours=1)
    assert dns_offer.url.startswith("https://shop.example/")
    assert detail.photo_url == "/media/ab/" + "a" * 64 + ".webp"
    kinds = [(event.store.name, event.kind) for event in detail.history]
    assert kinds[:4] == [("DNS", EventKind.CHECK_FAILED), ("Ozon", EventKind.BACK_IN_STOCK), ("Ozon", EventKind.PRICE_CHANGED), ("Ozon", EventKind.OUT_OF_STOCK)]
    assert ("Ушедший", EventKind.PRICE_CHANGED) in kinds  # история заменённой ссылки сохраняется
    assert {store.name for store in detail.chart_stores} == {"Ozon", "DNS", "Ушедший"}


def test_failure_reason_when_price_shown_after_failed_check(engine: Engine) -> None:
    world = World(engine)
    lamp = world.item(world.category("Дом"), "Лампа")
    link = world.link(lamp, world.store("DNS"))
    world.seen(link, YESTERDAY, 990_00)
    world.seen(link, NOW - timedelta(hours=1), status="BLOCKED")

    detail = item_detail(SqlUnitOfWork(engine), world.user, lamp, NOW)

    assert detail.failure_reason is not None and detail.unit_price_text == "990\xa0₽"


def test_store_chart_and_unknown_store(engine: Engine) -> None:
    world = World(engine)
    lamp = world.item(world.category("Дом"), "Лампа", quantity=5)
    shop, other = world.store("Ozon"), world.store("Чужой")
    world.seen(world.link(lamp, shop), NOW - 2 * DAY, 1000_00)

    points = item_chart(SqlUnitOfWork(engine), world.user, lamp, shop, ChartRange.WEEK, NOW)

    assert len(points) == 7 and points[-1].value_minor == 1000_00  # цена за штуку: количество не влияет
    with pytest.raises(PriceWatcherError) as caught:
        item_chart(SqlUnitOfWork(engine), world.user, lamp, other, ChartRange.WEEK, NOW)
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_quantity_change_with_version_and_conflict(engine: Engine) -> None:
    world = World(engine)
    lamp = world.item(world.category("Дом"), "Лампа")
    start = version(engine, lamp)

    changed = update_item_settings(SqlUnitOfWork(engine), world.user, lamp, start, NOW, quantity=3)

    assert (changed.quantity, changed.version) == (3, start + 1)
    with pytest.raises(PriceWatcherError) as caught:
        update_item_settings(SqlUnitOfWork(engine), world.user, lamp, start, NOW, quantity=4)
    assert caught.value.code is ErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize("changes", [{}, {"quantity": 0}, {"quantity": 1000}])
def test_bad_changes_are_refused(engine: Engine, changes: dict) -> None:
    world = World(engine)
    lamp = world.item(world.category("Дом"), "Лампа")

    with pytest.raises(PriceWatcherError) as caught:
        update_item_settings(SqlUnitOfWork(engine), world.user, lamp, version(engine, lamp), NOW, **changes)

    assert caught.value.code is ErrorCode.VALIDATION_ERROR


def test_stale_price_goes_back_to_total_with_the_same_mark(engine: Engine) -> None:
    world = World(engine)
    lamp = world.item(world.category("Дом"), "Лампа")
    world.seen(world.link(lamp, world.store()), NOW - 10 * DAY, 700_00)
    assert dashboard(SqlUnitOfWork(engine), world.user, Period.DAY, NOW).total_minor == 0

    on = update_item_settings(SqlUnitOfWork(engine), world.user, lamp, version(engine, lamp), NOW, in_total=True)
    assert (on.in_total, on.include_stale, on.price_status) == (True, True, PriceStatus.STALE)
    assert dashboard(SqlUnitOfWork(engine), world.user, Period.DAY, NOW).total_minor == 700_00

    off = update_item_settings(SqlUnitOfWork(engine), world.user, lamp, on.version, NOW, in_total=False)
    assert (off.in_total, off.include_stale) == (False, False)


def test_fresh_price_from_worker_ends_stale_mode(engine: Engine) -> None:
    world = World(engine)
    lamp = world.item(world.category("Дом"), "Лампа", include_stale=True)
    link = world.link(lamp, world.store())
    world.seen(link, NOW - 10 * DAY, 700_00)
    key = b"item-test-key-" + b"k" * 30
    worker = create_worker(SqlUnitOfWork(engine), key, "w", NOW)
    assert worker
    worker_id = world.one("SELECT id FROM workers WHERE name = 'w'")
    on_add = SqlUnitOfWork(engine)
    with on_add:
        on_add.queue.enqueue_on_add(link, NOW)
        on_add.commit()
    task = claim_task(SqlUnitOfWork(engine), worker_id, NOW)
    assert task is not None

    submit_result(SqlUnitOfWork(engine), worker_id, task.task_id, WorkerResult("ok", 650_00, availability="in_stock", source="site_data"), NOW)

    assert item_detail(SqlUnitOfWork(engine), world.user, lamp, NOW).include_stale is False


def test_someone_elses_or_hidden_item_is_not_found(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    home = owner.category("Дом")
    gone_group = owner.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position, deleted_at) VALUES (:u, :c, 'Г', 'г', 0, now()) RETURNING id", u=owner.user.id, c=home)
    lamp, hidden = owner.item(home, "Лампа"), owner.item(home, "В удалённой группе", group=gone_group)

    for user, item in ((stranger.user, lamp), (owner.user, hidden)):
        with pytest.raises(PriceWatcherError) as caught:
            item_detail(SqlUnitOfWork(engine), user, item, NOW)
        assert caught.value.code is ErrorCode.NOT_FOUND


def test_purchased_item_has_a_card_too(engine: Engine) -> None:
    world = World(engine)
    bought = world.item(world.category("Дом"), "Куплено", state="purchased")

    assert item_detail(SqlUnitOfWork(engine), world.user, bought, NOW).state.value == "purchased"
