"""Магазины у администратора на настоящей базе: обзор, название и цвет, отключение и его действие, удаление неиспользуемого, права, журнал."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, World
from test_user_decisions import watch

from pricewatch.application.item import item_detail
from pricewatch.application.stores_admin import change_store, delete_store, set_store_enabled, stores_overview
from pricewatch.domain.accounts import Role
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


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


def error(action) -> tuple[ErrorCode, object]:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code, caught.value.details


def audit(world: World) -> list[tuple]:
    with world.engine.connect() as db:
        return [tuple(row) for row in db.execute(text("SELECT action, before_json->>'name', after_json->>'name' FROM audit_events WHERE actor_id = :u ORDER BY id"),
                                                {"u": world.user.id})]


def test_overview_counts_links_and_watchers(engine: Engine) -> None:
    admin, other = World(engine, Role.ADMIN), World(engine)
    ozon, empty = admin.store("Ozon"), admin.store("Пустой")
    link = admin.link(admin.item(admin.category("Дом"), "Лампа"), ozon)
    watch(other, link, ozon)
    admin.link(admin.item(admin.category("Офис"), "Стул"), ozon, until=NOW - DAY)  # закрытая привязка — ссылка есть, слежения нет

    stores = {store.display_name: store for store in stores_overview(uow(engine), admin.user)}

    assert (stores["Ozon"].link_count, stores["Ozon"].watcher_count, stores["Ozon"].used) == (2, 2, True)
    assert (stores["Пустой"].link_count, stores["Пустой"].watcher_count, stores["Пустой"].used) == (0, 0, False)
    assert list(stores) == ["Ozon", "Пустой"] and stores["Пустой"].store_id == empty


def test_change_name_and_color(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)
    store = admin.store("ozon.ru")

    changed = change_store(uow(engine), admin.user, store, name="  Ozon   Маркет ", color="store-7", now=NOW)

    assert (changed.display_name, changed.color_token) == ("Ozon Маркет", "store-7")
    assert error(lambda: change_store(uow(engine), admin.user, store, name="  ", color="store-1", now=NOW))[0] is ErrorCode.VALIDATION_ERROR
    assert error(lambda: change_store(uow(engine), admin.user, store, name="Ozon", color="store-9", now=NOW)) == (
        ErrorCode.VALIDATION_ERROR, {"fields": {"color": "invalid"}}
    )
    assert error(lambda: change_store(uow(engine), admin.user, 10**12, name="Ozon", color="store-1", now=NOW))[0] is ErrorCode.NOT_FOUND
    assert audit(admin) == [("store_changed", "ozon.ru", "Ozon Маркет")]


def test_disabled_store_leaves_total_and_returns_after_enable(engine: Engine) -> None:
    admin, user = World(engine, Role.ADMIN), World(engine)
    store = admin.store("Ozon")
    item = user.item(user.category("Дом"), "Лампа")
    user.seen(user.link(item, store), NOW - DAY, 1000_00)

    set_store_enabled(uow(engine), admin.user, store, False, NOW)
    off = (user.board().total_minor, item_detail(uow(engine), user.user, item, NOW).price_status)
    set_store_enabled(uow(engine), admin.user, store, True, NOW)

    assert off == (0, PriceStatus.STORE_DISABLED)
    assert user.board().total_minor == 1000_00
    assert [action for action, *_ in audit(admin)] == ["store_disabled", "store_enabled"]


def test_delete_only_unused(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)
    used, unused = admin.store("Ozon"), admin.store("Пустой")
    admin.link(admin.item(admin.category("Дом"), "Лампа"), used)

    assert error(lambda: delete_store(uow(engine), admin.user, used, NOW)) == (ErrorCode.VALIDATION_ERROR, {"fields": {"store": "in_use"}})
    delete_store(uow(engine), admin.user, unused, NOW)

    assert [store.display_name for store in stores_overview(uow(engine), admin.user)] == ["Ozon"]
    assert audit(admin) == [("store_deleted", "Пустой", None)]


def test_only_active_admin(engine: Engine) -> None:
    user = World(engine)
    store = user.store()

    for action in (lambda: stores_overview(uow(engine), user.user),
                   lambda: change_store(uow(engine), user.user, store, name="X", color="store-1", now=NOW),
                   lambda: set_store_enabled(uow(engine), user.user, store, False, NOW),
                   lambda: delete_store(uow(engine), user.user, store, NOW)):
        assert error(action)[0] is ErrorCode.FORBIDDEN
