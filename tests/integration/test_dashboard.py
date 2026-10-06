"""Главная на настоящей базе: итог, изменение «только из-за цен», карточки, панели, проверка, окно наблюдений.

У модуля своя база: число изменений «на проверке» и последний запуск — общие для всей базы.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.dashboard import Panel, dashboard, panel_items
from pricewatch.domain.accounts import Role, User
from pricewatch.domain.days import Period
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import Direction
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # 15:00 по Москве
YESTERDAY = NOW - timedelta(days=1)
DAY = timedelta(days=1)


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings CASCADE"))


class World:
    """Строит данные одного пользователя: категории, товары, магазины, ссылки и наблюдения."""

    def __init__(self, engine: Engine, role: Role = Role.USER) -> None:
        self.engine = engine
        self.user = User(self.one("INSERT INTO users (login, password_hash, role, display_name) VALUES ('u' || substr(md5(random()::text), 1, 8), 'h', :r, 'U') RETURNING id", r=role.value), "u", role, "U", False)
        self.run = self.one("INSERT INTO parse_runs (kind, status, total, created_at, finished_at) VALUES ('on_add', 'finished', 0, :t, :t) RETURNING id", t=NOW - 400 * DAY)
        self.positions = 0

    def one(self, sql: str, **params: object) -> int:
        with self.engine.begin() as db:
            return db.execute(text(sql), params).scalar_one()

    def category(self, name: str, deleted: bool = False) -> int:
        self.positions += 1
        return self.one(
            "INSERT INTO categories (user_id, name, name_normalized, position, deleted_at) VALUES (:u, :n, lower(:n), :p, CASE WHEN :d THEN now() END) RETURNING id",
            u=self.user.id, n=name, p=self.positions, d=deleted,
        )

    def store(self, name: str = "Магазин", enabled: bool = True, color: str = "store-1") -> int:
        return self.one(
            "INSERT INTO stores (canonical_host, display_name, color, parser_key, enabled, auto_created, min_delay_ms) "
            "VALUES (substr(md5(random()::text), 1, 8) || '.example', :n, :c, 'universal', :e, false, 2000) RETURNING id",
            n=name, c=color, e=enabled,
        )

    def item(self, category: int, name: str, quantity: int = 1, in_total: bool = True, include_stale: bool = False, state: str = "active",
             group: int | None = None) -> int:
        self.positions += 1
        return self.one(
            "INSERT INTO items (user_id, category_id, group_id, name, quantity, in_total, include_stale, state, purchased_at, position) "
            "VALUES (:u, :c, :g, :n, :q, :t, :s, :st, CASE WHEN :st = 'purchased' THEN :now END, :p) RETURNING id",
            u=self.user.id, c=category, g=group, n=name, q=quantity, t=in_total, s=include_stale, st=state, now=NOW - DAY, p=self.positions,
        )

    def link(self, item: int, store: int, since: datetime = NOW - 60 * DAY, until: datetime | None = None) -> int:
        link_id = self.one(
            "INSERT INTO tracked_links (store_id, normalized_url, region_code) VALUES (:s, 'https://shop.example/' || md5(random()::text), 'spb') RETURNING id",
            s=store,
        )
        self.one(
            "INSERT INTO item_links (user_id, item_id, tracked_link_id, store_id, valid_from, valid_to) VALUES (:u, :i, :l, :s, :f, :t) RETURNING id",
            u=self.user.id, i=item, l=link_id, s=store, f=since, t=until,
        )
        return link_id

    def seen(self, link: int, at: datetime, price: int | None = None, card: int | None = None, availability: str = "in_stock",
             status: str = "ok", confirmation: str = "confirmed") -> None:
        task = self.one(
            "INSERT INTO check_tasks (run_id, tracked_link_id, status, finished_at, created_at) VALUES (:r, :l, 'done', :t, :t) RETURNING id",
            r=self.run, l=link, t=at,
        )
        self.one(
            "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, card_price_minor, availability, region_code, confirmation, source) "
            "VALUES (:l, :k, :t, :s, :p, :c, :a, 'spb', :cf, CASE WHEN :s = 'ok' THEN 'site_data' END) RETURNING id",
            l=link, k=task, t=at, s=status, p=price, c=card, a=availability, cf=confirmation,
        )

    def board(self, period: Period = Period.DAY):  # type: ignore[no-untyped-def]
        return dashboard(SqlUnitOfWork(self.engine), self.user, period, NOW)


def test_empty_list(engine: Engine) -> None:
    board = World(engine).board()

    assert (board.total_minor, board.total_text, board.change, board.categories) == (0, "0\xa0₽", None, [])
    assert (board.panels.tracked, board.panels.cheaper, board.panels.pricier, board.panels.out_of_stock) == (0, 0, 0, 0)
    assert (board.run.status, board.run.run_id, board.run.can_start) == ("idle", None, False)
    assert board.reviews_pending is None
    assert str(board.as_of) == "2026-10-06"


def test_total_change_cards_and_panels(engine: Engine) -> None:
    world = World(engine)
    home, office = world.category("Дом"), world.category("Офис")
    shop = world.store("Озон", color="store-3")
    lamp, kettle, watch_only, gone, bare = (
        world.item(home, "Лампа", quantity=2), world.item(home, "Чайник"), world.item(home, "Колонка", in_total=False),
        world.item(home, "Фен"), world.item(home, "Без ссылок"),
    )
    lamp_link = world.link(lamp, shop)
    world.seen(lamp_link, YESTERDAY, 10_000_00)
    world.seen(lamp_link, NOW - timedelta(hours=1), 9_000_00, card=8_000_00)  # действует цена по карте
    kettle_link = world.link(kettle, shop)
    world.seen(kettle_link, YESTERDAY, 3_000_00)
    world.seen(kettle_link, NOW - timedelta(hours=2), 3_300_00)
    world.seen(world.link(watch_only, shop), YESTERDAY, 5_000_00)
    world.seen(world.link(gone, shop), NOW - timedelta(hours=3), availability="out_of_stock")

    board = world.board()

    assert board.total_minor == 2 * 8_000_00 + 3_300_00
    assert board.total_text == "19\u202f300\xa0₽"
    assert board.change is not None and board.change.change_minor == (16_000_00 + 3_300_00) - (20_000_00 + 3_000_00)
    assert board.change.direction is Direction.DOWN and board.change.change_text == "−3\u202f700\xa0₽"
    assert (board.in_total_count, board.watching_count) == (4, 1)  # «Без ссылок» в итоге, но без цены
    card = board.categories[0]
    assert [c.name for c in board.categories] == ["Дом", "Офис"]
    assert (card.total_minor, card.in_total_count, card.watching_count) == (19_300_00, 4, 1)
    assert card.top_mover is not None and (card.top_mover.name, card.top_mover.percent_text, card.top_mover.direction) == ("Лампа", "20,0%", Direction.DOWN)
    assert len(card.sparkline) == 30 and card.sparkline[-1].value_minor == 19_300_00
    assert card.sparkline[0].value_minor is None  # месяц назад цен ещё не было
    empty = board.categories[1]
    assert (empty.total_minor, empty.change, empty.sparkline, empty.top_mover) == (0, None, [], None)
    assert (board.panels.tracked, board.panels.cheaper, board.panels.pricier, board.panels.out_of_stock) == (4, 1, 1, 1)


def test_panel_lists_sorted_paged_with_store_and_category(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    shop = world.store("DNS", color="store-2")
    for index, (old, new) in enumerate([(1000_00, 900_00), (1000_00, 500_00), (2000_00, 1800_00)]):
        link = world.link(world.item(home, f"Товар {index}"), shop)
        world.seen(link, YESTERDAY, old)
        world.seen(link, NOW - timedelta(hours=1), new)

    first = panel_items(SqlUnitOfWork(engine), world.user, Panel.CHEAPER, Period.DAY, NOW, offset=0, limit=2)
    second = panel_items(SqlUnitOfWork(engine), world.user, Panel.CHEAPER, Period.DAY, NOW, offset=2, limit=2)

    assert [row.name for row in first.items] == ["Товар 1", "Товар 2"]  # −50% раньше −10%; при равных −10% — больше в рублях
    assert first.next_offset == 2 and [row.name for row in second.items] == ["Товар 0"] and second.next_offset is None
    row = first.items[0]
    assert (row.old_price_text, row.new_price_text, row.category_name) == ("1\u202f000\xa0₽", "500\xa0₽", "Дом")
    assert row.store is not None and (row.store.name, row.store.color_token) == ("DNS", "store-2")


@pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0), (0, 101)])
def test_panel_paging_limits(engine: Engine, offset: int, limit: int) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        panel_items(SqlUnitOfWork(engine), World(engine).user, Panel.TRACKED, Period.DAY, NOW, offset, limit)

    assert caught.value.code is ErrorCode.VALIDATION_ERROR


def test_hidden_and_purchased_items_are_not_counted(engine: Engine) -> None:
    world = World(engine)
    home, deleted_category = world.category("Дом"), world.category("Старое", deleted=True)
    group = world.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position, deleted_at) VALUES (:u, :c, 'Г', 'г', 0, now()) RETURNING id", u=world.user.id, c=home)
    shop = world.store()
    for item in (world.item(home, "Куплен", state="purchased"), world.item(home, "Архив", state="archived"),
                 world.item(home, "В удалённой группе", group=group), world.item(deleted_category, "В удалённой категории")):
        world.seen(world.link(item, shop), NOW - timedelta(hours=1), 100_00)

    board = world.board()

    assert board.total_minor == 0 and [card.name for card in board.categories] == ["Дом"]
    assert board.panels.tracked == 0


def test_stale_price_counts_only_when_included_again(engine: Engine) -> None:
    world = World(engine)
    home = world.category("Дом")
    shop = world.store()
    old = NOW - 40 * DAY  # раньше окна наблюдений главной: берётся как последняя успешная
    world.seen(world.link(world.item(home, "Забыт"), shop), old, 500_00)
    world.seen(world.link(world.item(home, "Снова в итоге", include_stale=True), shop), old, 700_00)

    board = world.board()

    assert board.total_minor == 700_00
    assert (board.in_total_count, board.watching_count) == (1, 1)  # у устаревшей отметка снята, пока не включат снова


def test_disabled_store_and_pending_review(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)
    home = admin.category("Дом")
    off, shop = admin.store("Выключен", enabled=False), admin.store("Работает")
    admin.seen(admin.link(admin.item(home, "У выключенного"), off), NOW - timedelta(hours=1), 900_00)
    link = admin.link(admin.item(home, "Скачок"), shop)
    admin.seen(link, YESTERDAY, 1000_00)
    admin.seen(link, NOW - timedelta(hours=1), 100_00, confirmation="pending")  # ждёт решения: цена не меняется

    board = admin.board()

    assert board.total_minor == 1000_00
    assert board.change is not None and board.change.change_minor == 0
    assert board.reviews_pending == 1


def test_run_status_for_admin_and_user(engine: Engine) -> None:
    admin, user = World(engine, Role.ADMIN), World(engine)
    admin.one("INSERT INTO parse_runs (kind, slot_key, status, total, done, started_at, created_at) VALUES ('scheduled', '2026-10-06T15:00', 'running', 10, 4, :t, :t) RETURNING id", t=NOW - timedelta(minutes=5))

    seen_by_admin, seen_by_user = admin.board().run, user.board().run

    assert (seen_by_admin.status, seen_by_admin.total, seen_by_admin.done, seen_by_admin.can_start, seen_by_admin.can_stop) == ("running", 10, 4, False, True)
    assert (seen_by_user.status, seen_by_user.can_start, seen_by_user.can_stop) == ("running", False, False)


def test_change_over_week_compares_with_start_of_week(engine: Engine) -> None:
    world = World(engine)
    link = world.link(world.item(world.category("Дом"), "Лампа"), world.store())
    world.seen(link, NOW - 10 * DAY, 2000_00)
    world.seen(link, NOW - 3 * DAY, 1500_00)
    world.seen(link, NOW - timedelta(hours=1), 1500_00)

    day, week = world.board(Period.DAY), world.board(Period.WEEK)

    assert day.change is not None and day.change.change_minor == 0
    assert week.change is not None and week.change.change_text == "−500\xa0₽" and week.change.percent_text == "25,0%"
