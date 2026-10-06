"""Редактирование категорий и групп на настоящей базе: имена, версии, удаление с переносом и отменой, порядок."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import NOW, World

from pricewatch.application.category import category_detail
from pricewatch.application.dashboard import dashboard
from pricewatch.application.shopping import (
    create_category, create_group, delete_category, delete_group, rename_category, rename_group, reorder_categories, reorder_groups, undo_delete,
)
from pricewatch.domain.days import Period
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.undo import UNDO_WINDOW, UndoKind
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"shopping-edit-key-" + b"k" * 30


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


def names(engine: Engine, world: World) -> list[str]:
    return [card.name for card in dashboard(uow(engine), world.user, Period.DAY, NOW).categories]


def errors(action) -> ErrorCode:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code


def test_create_rename_and_name_taken(engine: Engine) -> None:
    world = World(engine)
    home = create_category(uow(engine), world.user, "  Дом  ")
    create_category(uow(engine), world.user, "Ёлка")

    renamed = rename_category(uow(engine), world.user, home.id, "Дача", home.version)

    assert (renamed.name, renamed.version) == ("Дача", home.version + 1)
    assert errors(lambda: create_category(uow(engine), world.user, "елка")) is ErrorCode.NAME_TAKEN
    assert errors(lambda: rename_category(uow(engine), world.user, home.id, "Снова", home.version)) is ErrorCode.VERSION_CONFLICT


def test_delete_empty_category_and_undo(engine: Engine) -> None:
    world = World(engine)
    home = create_category(uow(engine), world.user, "Дом")

    token = delete_category(uow(engine), KEY, world.user, home.id, home.version, None, NOW)
    assert names(engine, world) == []

    assert undo_delete(uow(engine), KEY, world.user, token, NOW + UNDO_WINDOW) == (UndoKind.CATEGORY, home.id)
    assert names(engine, world) == ["Дом"]


def test_undo_expires_and_is_personal(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    home = create_category(uow(engine), owner.user, "Дом")
    token = delete_category(uow(engine), KEY, owner.user, home.id, home.version, None, NOW)

    assert errors(lambda: undo_delete(uow(engine), KEY, owner.user, token, NOW + UNDO_WINDOW + timedelta(seconds=1))) is ErrorCode.NOT_FOUND
    assert errors(lambda: undo_delete(uow(engine), KEY, stranger.user, token, NOW)) is ErrorCode.NOT_FOUND


def test_delete_with_moving_items(engine: Engine) -> None:
    world = World(engine)
    old, new = world.category("Старая"), world.category("Новая")
    group = world.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, 'Г', 'г', 0) RETURNING id", u=world.user.id, c=old)
    world.item(new, "Уже был")
    world.item(old, "Без группы")
    world.item(old, "В группе", group=group)
    version = world.one("SELECT version FROM categories WHERE id = :c", c=old)

    delete_category(uow(engine), KEY, world.user, old, version, new, NOW)

    detail = category_detail(uow(engine), world.user, new, Period.DAY, NOW)
    assert [row.name for row in detail.groups[0].items] == ["Уже был", "Без группы", "В группе"]
    assert names(engine, world) == ["Новая"]


def test_delete_with_items_hides_them_and_undo_brings_back(engine: Engine) -> None:
    world = World(engine)
    old = world.category("Старая")
    world.item(old, "Лампа")
    version = world.one("SELECT version FROM categories WHERE id = :c", c=old)

    token = delete_category(uow(engine), KEY, world.user, old, version, None, NOW)
    assert dashboard(uow(engine), world.user, Period.DAY, NOW).watching_count + dashboard(uow(engine), world.user, Period.DAY, NOW).in_total_count == 0

    undo_delete(uow(engine), KEY, world.user, token, NOW)
    assert [row.name for row in category_detail(uow(engine), world.user, old, Period.DAY, NOW).groups[0].items] == ["Лампа"]


def test_move_target_must_be_another_own_category(engine: Engine) -> None:
    owner, stranger = World(engine), World(engine)
    home = owner.category("Дом")
    foreign = stranger.category("Чужая")
    version = owner.one("SELECT version FROM categories WHERE id = :c", c=home)

    assert errors(lambda: delete_category(uow(engine), KEY, owner.user, home, version, home, NOW)) is ErrorCode.VALIDATION_ERROR
    assert errors(lambda: delete_category(uow(engine), KEY, owner.user, home, version, foreign, NOW)) is ErrorCode.NOT_FOUND
    assert names(engine, owner) == ["Дом"]


def test_groups_create_rename_delete_keep_or_drop_items(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    lamps = create_group(uow(engine), world.user, home, "Лампы")
    renamed = rename_group(uow(engine), world.user, lamps.id, "Свет", lamps.version)
    world.item(home, "Лампа", group=lamps.id)
    kept = create_group(uow(engine), world.user, home, "Временная")
    world.item(home, "Кабель", group=kept.id)

    delete_group(uow(engine), KEY, world.user, kept.id, kept.version, True, NOW)
    token = delete_group(uow(engine), KEY, world.user, lamps.id, renamed.version, False, NOW)

    blocks = category_detail(uow(engine), world.user, home, Period.DAY, NOW).groups
    assert [(block.name, [row.name for row in block.items]) for block in blocks] == [(None, ["Кабель"])]
    undo_delete(uow(engine), KEY, world.user, token, NOW)
    blocks = category_detail(uow(engine), world.user, home, Period.DAY, NOW).groups
    assert [(block.name, [row.name for row in block.items]) for block in blocks] == [(None, ["Кабель"]), ("Свет", ["Лампа"])]


def test_reorder_categories_and_groups_with_versions(engine: Engine) -> None:
    world = World(engine)
    first, second, third = world.category("А"), world.category("Б"), world.category("В")
    order = world.one("SELECT categories_order_version FROM users WHERE id = :u", u=world.user.id)

    new_order = reorder_categories(uow(engine), world.user, [third, first, second], order)

    assert names(engine, world) == ["В", "А", "Б"] and new_order == order + 1
    assert errors(lambda: reorder_categories(uow(engine), world.user, [first, second, third], order)) is ErrorCode.VERSION_CONFLICT
    assert errors(lambda: reorder_categories(uow(engine), world.user, [first, second], new_order)) is ErrorCode.VALIDATION_ERROR
    a, b = create_group(uow(engine), world.user, first, "Первая"), create_group(uow(engine), world.user, first, "Вторая")
    category_version = world.one("SELECT version FROM categories WHERE id = :c", c=first)
    reorder_groups(uow(engine), world.user, first, [b.id, a.id], category_version)
    assert [block.name for block in category_detail(uow(engine), world.user, first, Period.DAY, NOW).groups] == ["Вторая", "Первая"]
