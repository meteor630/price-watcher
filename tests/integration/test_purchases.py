"""Покупка и архив на настоящей базе: переходы, отмена, цена в момент добавления, проверки формы, вид на страницах."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, World

from pricewatch.application.category import category_detail
from pricewatch.application.item import item_detail
from pricewatch.application.price_view import StoreView
from pricewatch.application.purchases import archive_item, archive_list, purchase_form, purchase_item, purchases_overview, return_to_list
from pricewatch.application.shopping import undo_delete
from pricewatch.domain.days import Period, day_start
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.shopping import ItemState
from pricewatch.domain.undo import UNDO_WINDOW, UndoKind
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"purchases-key-" + b"k" * 30
TODAY = date(2026, 10, 6)  # NOW — 15:00 по Москве 6 октября


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings CASCADE"))


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


def added(world: World, category: int, name: str, at: object = NOW - 10 * DAY, quantity: int = 1) -> int:
    item = world.item(category, name, quantity=quantity)
    world.one("UPDATE items SET created_at = :t WHERE id = :i RETURNING id", t=at, i=item)
    return item


def version(world: World, item: int) -> int:
    return world.one("SELECT version FROM items WHERE id = :i", i=item)


def stored(engine: Engine, item: int) -> dict[str, object]:
    with engine.connect() as db:
        return dict(db.execute(text(
            "SELECT state, purchased_at, purchase_price_minor, purchase_quantity, purchase_store_id, purchase_baseline_minor, archived_at "
            "FROM items WHERE id = :i"), {"i": item}).mappings().one())


def buy(engine: Engine, world: World, item: int, price: int = 85_000, quantity: int = 1, store: int | None = None, day: date = TODAY,
        at_version: int | None = None) -> str:
    return purchase_item(uow(engine), KEY, world.user, item, version(world, item) if at_version is None else at_version, price_minor=price,
                         quantity=quantity, store_id=store, day=day, now=NOW)


def code(action) -> tuple[ErrorCode, object]:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code, caught.value.details


def names(engine: Engine, world: World, category: int) -> list[str]:
    return [row.name for block in category_detail(uow(engine), world.user, category, Period.DAY, NOW).groups for row in block.items]


def test_purchase_saves_fields_and_price_at_adding(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    shop = world.store("Регард")
    lamp, chair = added(world, home, "Лампа", quantity=3), added(world, home, "Стул")
    link = world.link(lamp, shop, since=NOW - 10 * DAY)
    world.seen(link, NOW - 12 * DAY, price=100_000)  # ссылка общая: цена известна уже в момент добавления
    world.seen(link, NOW - 5 * DAY, price=90_000)

    token = buy(engine, world, lamp, price=85_000, quantity=2, store=shop)

    assert stored(engine, lamp) == {
        "state": "purchased", "purchased_at": day_start(TODAY), "purchase_price_minor": 85_000, "purchase_quantity": 2,
        "purchase_store_id": shop, "purchase_baseline_minor": 100_000, "archived_at": None,
    }
    assert token.startswith(f"{UndoKind.PURCHASE.value}.{lamp}.")
    assert names(engine, world, home) == ["Стул"]
    assert world.board().total_minor == 0
    bought = category_detail(uow(engine), world.user, home, Period.DAY, NOW).purchased
    assert [(view.name, view.quantity, view.total_text, view.store.name if view.store else None, view.purchased_on) for view in bought] == [
        ("Лампа", 2, "1\u202f700\xa0₽", "Регард", TODAY)
    ]
    assert chair not in [view.item_id for view in bought]


def test_price_at_adding_is_first_confirmed_check_or_none(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    shop = world.store()
    later, never = added(world, home, "Позже"), added(world, home, "Без цены")
    link = world.link(later, shop, since=NOW - 10 * DAY)
    world.seen(link, NOW - 3 * DAY, price=50_000)
    world.link(never, shop, since=NOW - 10 * DAY)

    buy(engine, world, later)
    buy(engine, world, never, store=shop)

    assert stored(engine, later)["purchase_baseline_minor"] == 50_000
    assert stored(engine, never)["purchase_baseline_minor"] is None


def test_undo_purchase_and_archive_returns_item_to_its_place(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    first, second = added(world, home, "Первый"), added(world, home, "Второй")

    bought = buy(engine, world, first)
    undo_delete(uow(engine), KEY, world.user, bought, NOW + timedelta(seconds=5))
    archived = archive_item(uow(engine), KEY, world.user, second, version(world, second), NOW)
    assert stored(engine, second)["archived_at"] == NOW
    undo_delete(uow(engine), KEY, world.user, archived, NOW)

    assert names(engine, world, home) == ["Первый", "Второй"]
    assert stored(engine, first) == {"state": "active", "purchased_at": None, "purchase_price_minor": None, "purchase_quantity": None,
                                     "purchase_store_id": None, "purchase_baseline_minor": None, "archived_at": None}
    # Второй раз отменять нечего; просроченный токен — тоже NOT_FOUND.
    assert code(lambda: undo_delete(uow(engine), KEY, world.user, bought, NOW)) == (ErrorCode.NOT_FOUND, {"undo": "changed"})
    late = buy(engine, world, first)
    assert code(lambda: undo_delete(uow(engine), KEY, world.user, late, NOW + UNDO_WINDOW + timedelta(seconds=1)))[0] is ErrorCode.NOT_FOUND


def test_transitions_follow_the_rules(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    item = added(world, home, "Лампа")
    invalid = (ErrorCode.VALIDATION_ERROR, {"fields": {"state": "invalid_transition"}})

    assert code(lambda: return_to_list(uow(engine), world.user, item, version(world, item))) == invalid
    buy(engine, world, item)
    assert code(lambda: archive_item(uow(engine), KEY, world.user, item, version(world, item), NOW)) == invalid
    assert code(lambda: buy(engine, world, item)) == invalid
    returned = return_to_list(uow(engine), world.user, item, version(world, item))
    archive_item(uow(engine), KEY, world.user, item, returned.version, NOW)
    assert code(lambda: buy(engine, world, item)) == invalid

    back = return_to_list(uow(engine), world.user, item, version(world, item))

    assert (back.state, back.purchase, back.archived_at) == (ItemState.ACTIVE, None, None)


def test_purchase_is_validated(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    home = owner.category("Дом")
    own_shop, other_shop = owner.store("Свой"), owner.store("Чужой")
    item = added(owner, home, "Лампа")
    owner.link(item, own_shop)

    def field(error: tuple[ErrorCode, object]) -> object:
        assert error[0] is ErrorCode.VALIDATION_ERROR
        return error[1]

    assert field(code(lambda: buy(engine, owner, item, price=0))) == {"fields": {"purchase_price": "out_of_range"}}
    assert field(code(lambda: buy(engine, owner, item, price=10**12 + 1))) == {"fields": {"purchase_price": "out_of_range"}}
    assert field(code(lambda: buy(engine, owner, item, quantity=1000))) == {"fields": {"purchase_quantity": "out_of_range"}}
    assert field(code(lambda: buy(engine, owner, item, day=TODAY + timedelta(days=1)))) == {"fields": {"purchased_at": "in_future"}}
    assert field(code(lambda: buy(engine, owner, item, day=date(1999, 12, 31)))) == {"fields": {"purchased_at": "too_early"}}
    assert field(code(lambda: buy(engine, owner, item, store=other_shop))) == {"fields": {"purchase_store": "not_item_store"}}
    assert code(lambda: buy(engine, owner, item, at_version=version(owner, item) + 1))[0] is ErrorCode.VERSION_CONFLICT
    assert code(lambda: purchase_item(uow(engine), KEY, stranger.user, item, version(owner, item), price_minor=1, quantity=1, store_id=None,
                                      day=TODAY, now=NOW))[0] is ErrorCode.NOT_FOUND
    assert code(lambda: archive_item(uow(engine), KEY, stranger.user, item, version(owner, item), NOW))[0] is ErrorCode.NOT_FOUND
    assert stored(engine, item)["state"] == "active"

    buy(engine, owner, item, price=10**12, quantity=999, store=own_shop, day=date(2000, 1, 1))
    assert stored(engine, item)["purchased_at"] == day_start(date(2000, 1, 1))


def test_form_defaults_to_best_price_and_item_stores(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    cheap, dear = world.store("Б-магазин"), world.store("А-магазин")
    item, bare = added(world, home, "Лампа", quantity=2), added(world, home, "Без цены")
    world.seen(world.link(item, cheap), NOW - DAY, price=99_950)
    world.seen(world.link(item, dear), NOW - DAY, price=120_000)

    form = purchase_form(uow(engine), world.user, item, NOW)
    empty = purchase_form(uow(engine), world.user, bare, NOW)

    assert (form.price_input, form.store_id, form.quantity, form.today) == ("999,50", cheap, 2, TODAY)
    assert [store.name for store in form.stores] == ["А-магазин", "Б-магазин"]
    assert (empty.price_input, empty.store_id, empty.stores) == ("", None, [])
    buy(engine, world, bare)
    assert code(lambda: purchase_form(uow(engine), world.user, bare, NOW))[0] is ErrorCode.VALIDATION_ERROR


def test_item_card_shows_purchase_and_cheaper_now_for_14_days(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    shop = world.store("Регард")
    item = added(world, home, "Лампа")
    link = world.link(item, shop)
    world.seen(link, NOW - 2 * DAY, price=100_000)
    buy(engine, world, item, price=100_000, store=shop, day=TODAY - timedelta(days=1))
    world.seen(link, NOW - DAY / 2, price=93_000)

    fresh = item_detail(uow(engine), world.user, item, NOW)
    late = item_detail(uow(engine), world.user, item, day_start(TODAY - timedelta(days=1)) + 14 * DAY)

    assert fresh.purchase is not None
    assert (fresh.purchase.price_text, fresh.purchase.quantity, fresh.purchase.store) == ("1\u202f000\xa0₽", 1, StoreView(shop, "Регард", "store-1"))
    assert (fresh.state, fresh.cheaper_now_minor, fresh.cheaper_now_text) == (ItemState.PURCHASED, 7_000, "70\xa0₽")
    assert late.cheaper_now_minor is None
    archived = added(world, home, "Архив")
    archive_item(uow(engine), KEY, world.user, archived, version(world, archived), NOW)
    assert item_detail(uow(engine), world.user, archived, NOW).archived_at == NOW


def test_purchase_in_deleted_group_is_hidden_with_it(engine: Engine) -> None:
    # Группа, удалённая вместе с товарами, скрывает и купленные: они вернутся с отменой удаления группы.
    world = World(engine)
    home = world.category("Дом")
    lamps = world.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, 'Лампы', 'лампы', 0) RETURNING id", u=world.user.id, c=home)
    lamp = world.item(home, "Лампа", group=lamps)
    world.one("UPDATE items SET created_at = :t WHERE id = :i RETURNING id", t=NOW - 10 * DAY, i=lamp)
    buy(engine, world, lamp)
    world.one("UPDATE groups SET deleted_at = now() WHERE id = :g RETURNING id", g=lamps)

    assert category_detail(uow(engine), world.user, home, Period.DAY, NOW).purchased == []



def test_overview_counts_period_from_moscow_midnight_with_signed_savings(engine: Engine) -> None:
    world = World(engine)
    home, office = world.category("Дом"), world.category("Офис")
    shop = world.store("Регард")
    saved, overpaid, no_base, old = (added(world, home, "Экономия"), added(world, office, "Переплата"), added(world, office, "Без цены"),
                                     added(world, home, "Давно"))
    for item, price in ((saved, 100_000), (overpaid, 50_000), (old, 10_000)):
        world.seen(world.link(item, shop, since=NOW - 10 * DAY), NOW - 12 * DAY, price=price)
    buy(engine, world, saved, price=90_000, quantity=2, store=shop, day=TODAY - timedelta(days=30))  # ровно начало «месяца»
    buy(engine, world, overpaid, price=60_000)
    buy(engine, world, no_base, price=5_000)
    buy(engine, world, old, price=9_000, day=TODAY - timedelta(days=31))

    month = purchases_overview(uow(engine), world.user, Period.MONTH, NOW)
    day = purchases_overview(uow(engine), world.user, Period.DAY, NOW)
    year = purchases_overview(uow(engine), world.user, Period.YEAR, NOW)

    assert (month.count, month.spent_minor, month.savings_minor, month.savings_text) == (3, 245_000, 10_000, "100\xa0₽")
    assert [entry.view.name for entry in month.entries] == ["Без цены", "Переплата", "Экономия"]
    assert [(entry.savings_minor, entry.savings_text) for entry in month.entries] == [(None, None), (-10_000, "100\xa0₽"), (20_000, "200\xa0₽")]
    assert [(c.name, c.count, c.spent_minor, c.savings_minor) for c in month.categories] == [("Дом", 1, 180_000, 20_000), ("Офис", 2, 65_000, -10_000)]
    assert (day.count, day.spent_minor, day.savings_minor) == (2, 65_000, -10_000)
    assert (year.count, year.savings_minor) == (4, 11_000)


def test_overview_and_archive_when_empty(engine: Engine) -> None:
    world = World(engine)
    world.category("Дом")

    overview = purchases_overview(uow(engine), world.user, Period.MONTH, NOW)

    assert (overview.count, overview.spent_text, overview.savings_minor, overview.categories, overview.entries) == (0, "0\xa0₽", None, [], [])
    assert archive_list(uow(engine), world.user, NOW) == []


def test_archive_lists_last_known_price_newest_first(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    home = owner.category("Дом")
    shop = owner.store("Регард")
    first, second, kept = added(owner, home, "Первый"), added(owner, home, "Второй"), added(owner, home, "В списке")
    owner.seen(owner.link(first, shop), NOW - 20 * DAY, price=77_700)  # давно не проверялась — цена устарела, но известна
    archive_item(uow(engine), KEY, owner.user, first, version(owner, first), NOW - DAY)
    archive_item(uow(engine), KEY, owner.user, second, version(owner, second), NOW)
    stranger_item = added(stranger, stranger.category("Чужое"), "Чужой")
    archive_item(uow(engine), KEY, stranger.user, stranger_item, version(stranger, stranger_item), NOW)

    entries = archive_list(uow(engine), owner.user, NOW)

    assert [(entry.row.name, entry.category_name, entry.archived_at) for entry in entries] == [("Второй", "Дом", NOW), ("Первый", "Дом", NOW - DAY)]
    assert (entries[1].row.unit_price_text, entries[1].row.price_status.value) == ("777\xa0₽", "stale")
    assert kept not in [entry.row.item_id for entry in entries]
