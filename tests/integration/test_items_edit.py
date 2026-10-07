"""Редактирование товаров на настоящей базе: создание, изменение и перенос, удаление с отменой, ссылки и их состояния, порядок."""

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import NOW, World

from pricewatch.application.category import category_detail
from pricewatch.application.shopping import (
    LinkStatus, add_links, create_item, delete_item, item_links_view, remove_link, reorder_items, undo_delete, update_item,
)
from pricewatch.domain.days import Period
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.undo import UNDO_WINDOW
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"items-edit-key-" + b"k" * 30
CATALOG = LinkCatalog()


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


def shop() -> str:
    return f"https://www.shop-{uuid.uuid4().hex[:8]}.example/p/lamp"


def rows(engine: Engine, world: World, category: int) -> list[tuple[str | None, list[str]]]:
    return [(block.name, [row.name for row in block.items]) for block in category_detail(uow(engine), world.user, category, Period.DAY, NOW).groups]


def code(action) -> ErrorCode:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code


def test_create_update_and_move_to_other_category(engine: Engine) -> None:
    world = World(engine)
    home, office = world.category("Дом"), world.category("Офис")
    lamps = world.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, 'Лампы', 'лампы', 0) RETURNING id", u=world.user.id, c=home)
    world.item(office, "Стул")
    item = create_item(uow(engine), world.user, home, lamps, "  Лампа  ", "Лампа настольная", 2)

    changed = update_item(uow(engine), world.user, item.id, item.version, name="Лампа LED", full_name=None, quantity=3, category_id=home, group_id=None)
    moved = update_item(uow(engine), world.user, item.id, changed.version, name="Лампа LED", full_name=None, quantity=3, category_id=office, group_id=None)

    assert (item.name, item.group_id) == ("Лампа", lamps)
    assert (changed.group_id, changed.quantity, changed.full_name) == (None, 3, None)
    assert moved.category_id == office and rows(engine, world, office) == [(None, ["Стул", "Лампа LED"])]
    assert code(lambda: update_item(uow(engine), world.user, item.id, item.version, name="x", full_name=None, quantity=1, category_id=home, group_id=None)) is ErrorCode.VERSION_CONFLICT


def test_move_needs_own_category_and_matching_group(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    home, other = owner.category("Дом"), owner.category("Другая")
    foreign = stranger.category("Чужая")
    lamps = owner.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, 'Лампы', 'лампы', 0) RETURNING id", u=owner.user.id, c=home)
    item = create_item(uow(engine), owner.user, home, None, "Лампа", None, 1)

    assert code(lambda: update_item(uow(engine), owner.user, item.id, item.version, name="Лампа", full_name=None, quantity=1, category_id=foreign, group_id=None)) is ErrorCode.NOT_FOUND
    assert code(lambda: update_item(uow(engine), owner.user, item.id, item.version, name="Лампа", full_name=None, quantity=1, category_id=other, group_id=lamps)) in (ErrorCode.NOT_FOUND, ErrorCode.VALIDATION_ERROR)


def test_delete_item_and_undo(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    item = create_item(uow(engine), world.user, home, None, "Лампа", None, 1)

    token = delete_item(uow(engine), KEY, world.user, item.id, item.version, NOW)
    assert rows(engine, world, home) == []

    undo_delete(uow(engine), KEY, world.user, token, NOW + UNDO_WINDOW)
    assert rows(engine, world, home) == [(None, ["Лампа"])]


def test_links_are_added_one_by_one_and_show_state(engine: Engine) -> None:
    world = World(engine)
    item = create_item(uow(engine), world.user, world.category("Дом"), None, "Лампа", None, 1)
    with engine.begin() as db:
        off_host = db.execute(text("SELECT canonical_host FROM stores WHERE id = :s"), {"s": world.store("Отключён", enabled=False)}).scalar_one()

    results = add_links(lambda: uow(engine), CATALOG, world.user, item.id, [shop(), "не адрес", f"https://{off_host}/p", "  "], NOW)

    assert [result.error is None for result in results] == [True, False, False]  # пустая строка пропущена
    assert results[2].error == "Этот сайт отключён администратором."
    (link,) = item_links_view(uow(engine), world.user, item.id)
    assert (link.status, link.url.startswith("https://www.shop-") or link.url.startswith("https://shop-")) == (LinkStatus.CHECKING, True)


def test_link_states_from_last_check(engine: Engine) -> None:
    world = World(engine)
    item = world.item(world.category("Дом"), "Лампа")
    shops = {name: world.store(name) for name in ("Найдено", "Нет цены", "Капча", "Новый", "Отключён")}
    links = {name: world.link(item, store) for name, store in shops.items()}
    world.seen(links["Найдено"], NOW - timedelta(hours=1), 1000_00, card=900_00)
    world.one("UPDATE price_observations SET source = 'page' WHERE tracked_link_id = :l RETURNING id", l=links["Найдено"])
    world.seen(links["Нет цены"], NOW - timedelta(hours=1), status="PRICE_NOT_FOUND")
    world.seen(links["Капча"], NOW - timedelta(hours=1), status="CAPTCHA")
    world.one("UPDATE stores SET enabled = false WHERE id = :s RETURNING id", s=shops["Отключён"])

    states = {view.store_name: view for view in item_links_view(uow(engine), world.user, item)}

    assert states["Найдено"].status is LinkStatus.FOUND and states["Найдено"].price_text == "900\xa0₽" and states["Найдено"].source_label == "найдено на странице"
    assert states["Нет цены"].status is LinkStatus.NOT_FOUND
    assert states["Капча"].status is LinkStatus.WAITING
    assert states["Новый"].status is LinkStatus.UNCHECKED
    assert states["Отключён"].status is LinkStatus.STORE_DISABLED


def test_remove_link_and_undo_unless_replaced(engine: Engine) -> None:
    world = World(engine)
    item = create_item(uow(engine), world.user, world.category("Дом"), None, "Лампа", None, 1)
    url = shop()
    add_links(lambda: uow(engine), CATALOG, world.user, item.id, [url], NOW - timedelta(minutes=10))
    (link,) = item_links_view(uow(engine), world.user, item.id)

    token = remove_link(uow(engine), KEY, world.user, item.id, link.item_link_id, NOW)
    assert item_links_view(uow(engine), world.user, item.id) == []
    undo_delete(uow(engine), KEY, world.user, token, NOW)
    assert [view.item_link_id for view in item_links_view(uow(engine), world.user, item.id)] == [link.item_link_id]

    token = remove_link(uow(engine), KEY, world.user, item.id, link.item_link_id, NOW + timedelta(minutes=1))
    add_links(lambda: uow(engine), CATALOG, world.user, item.id, [url.replace("lamp", "lamp-2")], NOW + timedelta(minutes=2))
    assert code(lambda: undo_delete(uow(engine), KEY, world.user, token, NOW + timedelta(minutes=1, seconds=5))) is ErrorCode.NOT_FOUND


def test_someone_elses_item_and_link(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    item = owner.item(owner.category("Дом"), "Лампа")
    link = owner.link(item, owner.store())
    binding = owner.one("SELECT id FROM item_links WHERE tracked_link_id = :l", l=link)

    assert code(lambda: item_links_view(uow(engine), stranger.user, item)) is ErrorCode.NOT_FOUND
    assert code(lambda: remove_link(uow(engine), KEY, stranger.user, item, binding, NOW)) is ErrorCode.NOT_FOUND
    assert code(lambda: add_links(lambda: uow(engine), CATALOG, stranger.user, item, [shop()], NOW)) is ErrorCode.NOT_FOUND


def test_reorder_items_in_group_and_ungrouped(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    first, second = world.item(home, "Первый"), world.item(home, "Второй")
    version = world.one("SELECT version FROM categories WHERE id = :c", c=home)

    reorder_items(uow(engine), world.user, home, None, [second, first], version)

    assert rows(engine, world, home) == [(None, ["Второй", "Первый"])]


def test_reorder_counts_only_items_in_list(engine: Engine) -> None:
    # Купленный и архивный товары не видны в настройках — порядок «в списке» передаётся без них.
    world = World(engine)
    home = world.category("Дом")
    first, second = world.item(home, "Первый"), world.item(home, "Второй")
    world.item(home, "Купленный", state="purchased")
    world.item(home, "Архивный", state="archived")
    version = world.one("SELECT version FROM categories WHERE id = :c", c=home)

    reorder_items(uow(engine), world.user, home, None, [second, first], version)

    assert rows(engine, world, home) == [(None, ["Второй", "Первый"])]
