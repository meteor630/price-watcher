"""tools/import_legacy.py на настоящей базе: перенос целиком, замена прежних категорий, отказ без следов."""

import importlib.util
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from sqlalchemy import Engine, text

from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.schema import alembic_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

# Схема старой базы (только таблицы, которые читает перенос).
LEGACY_SCHEMA = """
CREATE TABLE categories (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, position INTEGER NOT NULL DEFAULT 0, archived_at TEXT);
CREATE TABLE stores (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL UNIQUE, color TEXT NOT NULL, method TEXT, card TEXT,
    auto_added TEXT, named INTEGER NOT NULL DEFAULT 1);
CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT NOT NULL, full TEXT, category_id TEXT REFERENCES categories(id), group_name TEXT,
    qty INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, archived_at TEXT, position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE listings (id INTEGER PRIMARY KEY, product_id TEXT NOT NULL, store_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
    ref_title TEXT, created_at TEXT NOT NULL, UNIQUE (product_id, store_id));
CREATE TABLE listing_urls (id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL, url TEXT NOT NULL, active_from TEXT NOT NULL, active_to TEXT);
CREATE TABLE runs (id INTEGER PRIMARY KEY, trigger TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, total INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE checks (id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL, url_id INTEGER, run_id INTEGER, checked_at TEXT NOT NULL,
    day TEXT NOT NULL, status TEXT NOT NULL, price REAL, card_price REAL, title TEXT, image TEXT, method TEXT, source TEXT, reason TEXT,
    decision TEXT);
"""


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_legacy", PROJECT_ROOT / "tools" / "import_legacy.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()


@pytest.fixture
def database_url(empty_database_url: str) -> str:
    """Своя база на тест: перенос выполняется в базу один раз, повторный отклоняется."""
    command.upgrade(alembic_config(empty_database_url), "head")
    return empty_database_url


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(database_url)
    yield db_engine
    db_engine.dispose()


def owner(engine: Engine) -> str:
    login = f"o{uuid.uuid4().hex[:10]}"
    with engine.begin() as db:
        db.execute(text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'admin', 'Владелец')"), {"l": login})
    return login


def legacy_db(tmp_path: Path, *, bad_price: bool = False) -> tuple[Path, str]:
    """Старая база: две категории (одна архивная, пустая), три товара, два магазина, замена адреса, подозрительные цены."""
    tag = uuid.uuid4().hex[:8]
    shop = f"shop-{tag}.example"
    path = tmp_path / "pricewatch.db"
    db = sqlite3.connect(path)
    db.executescript(LEGACY_SCHEMA)
    db.executemany("INSERT INTO categories VALUES (?, ?, ?, ?)", [
        ("dom", "Дом", 0, None), ("staroe", "Старое", 1, "2026-10-03T00:00:00+00:00"),
    ])
    db.executemany("INSERT INTO stores (id, name, host, color, method) VALUES (?, ?, ?, ?, ?)", [
        ("dns", "ДНС старое имя", "dns-shop.ru", "#f08a24", "browser"), ("citilink", "Ситилинк", shop, "#4c8dff", "http"),
    ])
    db.executemany("INSERT INTO products (id, name, full, category_id, group_name, qty, created_at, archived_at, position) VALUES (?,?,?,?,?,?,?,?,?)", [
        ("lampa", "Лампа", "Лампа настольная полное", "dom", "Свет", 2, "2026-10-02T09:00:00+00:00", None, 0),
        ("chaynik", "Чайник", None, "dom", None, 0, "2026-10-02T09:00:00+00:00", None, 1),
        ("torsher", "Старый торшер", None, "dom", "Свет", 1, "2026-10-02T09:00:00+00:00", "2026-10-05T10:00:00+00:00", 2),
    ])
    db.executemany("INSERT INTO listings VALUES (?, ?, ?, ?, ?, ?)", [
        (1, "lampa", "citilink", "active", "Лампа настольная", "2026-10-02T10:00:00+00:00"),
        (2, "chaynik", "dns", "active", None, "2026-10-02T10:00:00+00:00"),
        (3, "torsher", "citilink", "archived", None, "2026-10-02T10:00:00+00:00"),
    ])
    db.executemany("INSERT INTO listing_urls VALUES (?, ?, ?, ?, ?)", [
        (1, 1, f"https://{shop}/p/lamp-{tag}?ysclid=1", "2026-10-02", "2026-10-04"),
        (2, 1, f"https://{shop}/p/lamp-skipped-{tag}", "2026-10-04", "2026-10-04"),
        (3, 1, f"https://{shop}/p/lamp-new-{tag}", "2026-10-04", None),
        (4, 2, f"https://www.dns-shop.ru/product/{tag}{tag}/chaynik/", "2026-10-02", None),
        (5, 3, f"https://{shop}/p/torsher-{tag}", "2026-10-02", None),
    ])
    db.executemany("INSERT INTO runs (id, trigger, started_at, finished_at, total, failed, cancelled) VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (1, "daily", "2026-10-03T06:00:00+00:00", "2026-10-03T06:10:00+00:00", 4, 1, 0),
        (2, "manual", "2026-10-04T12:00:00+00:00", "2026-10-04T12:05:00+00:00", 3, 0, 1),
    ])
    db.executemany(
        "INSERT INTO checks (id, listing_id, url_id, run_id, checked_at, day, status, price, card_price, title, source, decision) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, 1, None, "2026-10-02T10:01:00+00:00", "2026-10-02", "ok", 1000.0, None, "Лампа настольная", "JSON-LD", None),
            (2, 1, 1, 1, "2026-10-03T06:01:00+00:00", "2026-10-03", "ok", 1000.005 if bad_price else 900.0, 850.0, "Лампа настольная", "JSON-LD", None),
            (3, 2, 4, 1, "2026-10-03T06:02:00+00:00", "2026-10-03", "out_of_stock", None, None, "Чайник", None, None),
            (4, 3, 5, 1, "2026-10-03T06:03:00+00:00", "2026-10-03", "ok", 5000.0, None, "Торшер", "на странице", None),
            (5, 2, 4, 1, "2026-10-03T06:04:00+00:00", "2026-10-03", "error", None, None, None, None, None),
            (6, 1, 3, 2, "2026-10-04T12:01:00+00:00", "2026-10-04", "ok", 950.0, None, "Лампа настольная", "microdata", None),
            (7, 2, 4, 2, "2026-10-04T12:02:00+00:00", "2026-10-04", "suspect", 4000.0, None, "Чайник", "JSON-LD", "rejected"),
            (8, 2, 4, 2, "2026-10-04T12:03:00+00:00", "2026-10-04", "suspect", 1500.0, None, "Чайник", "JSON-LD", None),
        ],
    )
    db.commit()
    db.close()
    return path, shop


def run_import(engine: Engine, path: Path, login: str, replace: bool = False):  # type: ignore[no-untyped-def]
    return tool.import_legacy(engine, tool.read_legacy(path), login, replace, NOW)


def rows(engine: Engine, sql: str, **params: object) -> list[tuple]:
    with engine.connect() as db:
        return [tuple(row) for row in db.execute(text(sql), params)]


def user_id(engine: Engine, login: str) -> int:
    return rows(engine, "SELECT id FROM users WHERE login = :l", l=login)[0][0]


# --- обычный путь ---


def test_everything_with_price_is_imported(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    summary = run_import(engine, path, login)
    assert summary == tool.Summary(
        replaced_categories=0, categories=1, groups=1, items=3, stores=2, links=4, runs=3, observations=7, decisions=1
    )


def test_items_keep_order_groups_quantities_and_archive(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    run_import(engine, path, login)
    found = rows(
        engine,
        "SELECT c.name, g.name, i.name, i.full_name, i.quantity, i.in_total, i.state, i.archived_at FROM items i "
        "JOIN categories c ON c.id = i.category_id LEFT JOIN groups g ON g.id = i.group_id WHERE i.user_id = :u ORDER BY i.id",
        u=user_id(engine, login),
    )
    assert found == [
        ("Дом", "Свет", "Лампа", "Лампа настольная полное", 2, True, "active", None),
        ("Дом", None, "Чайник", None, 1, False, "active", None),
        ("Дом", "Свет", "Старый торшер", None, 1, True, "archived", datetime(2026, 10, 5, 10, tzinfo=UTC)),
    ]


def test_replaced_address_continues_history_without_skipped_one(engine: Engine, tmp_path: Path) -> None:
    path, shop = legacy_db(tmp_path)
    tag = shop.removeprefix("shop-").removesuffix(".example")
    login = owner(engine)
    run_import(engine, path, login)
    links = rows(
        engine,
        "SELECT t.normalized_url, l.valid_from, l.valid_to FROM item_links l JOIN tracked_links t ON t.id = l.tracked_link_id "
        "JOIN items i ON i.id = l.item_id WHERE l.user_id = :u AND i.name = 'Лампа' ORDER BY l.valid_from",
        u=user_id(engine, login),
    )
    # Адрес приведён к одному виду (без ysclid); заменённый до первой проверки не перенесён; граница — первая проверка нового.
    assert links == [
        (f"https://{shop}/p/lamp-{tag}", datetime(2026, 10, 2, 10, tzinfo=UTC), datetime(2026, 10, 4, 12, 1, tzinfo=UTC)),
        (f"https://{shop}/p/lamp-new-{tag}", datetime(2026, 10, 4, 12, 1, tzinfo=UTC), None),
    ]


def test_prices_are_exact_and_suspects_wait_or_keep_rejection(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    run_import(engine, path, login)
    found = rows(
        engine,
        "SELECT i.name, o.price_minor, o.card_price_minor, o.availability, o.confirmation, o.source, d.decision FROM price_observations o "
        "JOIN item_links l ON l.tracked_link_id = o.tracked_link_id AND o.checked_at >= l.valid_from AND (l.valid_to IS NULL OR o.checked_at < l.valid_to) "
        "JOIN items i ON i.id = l.item_id LEFT JOIN price_decisions d ON d.observation_id = o.id "
        "WHERE l.user_id = :u ORDER BY o.checked_at",
        u=user_id(engine, login),
    )
    assert found == [
        ("Лампа", 100_000, None, "in_stock", "confirmed", "site_data", None),
        ("Лампа", 90_000, 85_000, "in_stock", "confirmed", "site_data", None),
        ("Чайник", None, None, "out_of_stock", "confirmed", None, None),
        ("Старый торшер", 500_000, None, "in_stock", "confirmed", "page", None),
        ("Лампа", 95_000, None, "in_stock", "confirmed", "site_data", None),
        ("Чайник", 400_000, None, "in_stock", "pending", "site_data", "reject"),
        ("Чайник", 150_000, None, "in_stock", "pending", "site_data", None),
    ]


def test_runs_are_finished_summarized_and_do_not_take_scheduler_slots(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    run_import(engine, path, login)
    found = rows(
        engine,
        "SELECT r.kind, r.slot_key LIKE 'legacy-%', r.requested_by = :u, r.status, r.done, r.failed, r.summarized_at = r.finished_at "
        "FROM parse_runs r WHERE r.id IN (SELECT t.run_id FROM check_tasks t JOIN item_links l ON l.tracked_link_id = t.tracked_link_id "
        "WHERE l.user_id = :u) ORDER BY r.created_at",
        u=user_id(engine, login),
    )
    assert found == [
        ("on_add", None, None, "finished", 1, 0, True),
        ("scheduled", True, None, "finished", 3, 1, True),
        ("manual", None, True, "finished", 3, 0, True),
    ]


def test_store_names_come_from_catalog_or_old_site(engine: Engine, tmp_path: Path) -> None:
    path, shop = legacy_db(tmp_path)
    run_import(engine, path, owner(engine))
    found = dict(rows(engine, "SELECT canonical_host, display_name FROM stores WHERE canonical_host IN ('dns-shop.ru', :s)", s=shop))
    assert found == {"dns-shop.ru": "DNS", shop: "Ситилинк"}
    assert rows(engine, "SELECT fetch_method FROM stores WHERE canonical_host = :s", s=shop) == [("http",)]


def test_baseline_title_comes_from_old_reference(engine: Engine, tmp_path: Path) -> None:
    path, shop = legacy_db(tmp_path)
    run_import(engine, path, owner(engine))
    assert rows(engine, "SELECT baseline_title FROM tracked_links WHERE normalized_url LIKE :u", u=f"https://{shop}/p/lamp-new-%") == [
        ("Лампа настольная",)
    ]


# --- замена прежних данных ---


def add_category(engine: Engine, login: str, name: str) -> None:
    with engine.begin() as db:
        db.execute(
            text("INSERT INTO categories (user_id, name, name_normalized, position) SELECT id, :n, lower(:n), 0 FROM users WHERE login = :l"),
            {"n": name, "l": login},
        )


def test_replace_deletes_previous_categories_like_the_site(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    add_category(engine, login, "Дом")
    summary = run_import(engine, path, login, replace=True)
    assert summary.replaced_categories == 1
    assert rows(
        engine, "SELECT name, deleted_at IS NOT NULL FROM categories WHERE user_id = :u ORDER BY id", u=user_id(engine, login)
    ) == [("Дом", True), ("Дом", False)]


# --- отказ ---


def test_existing_categories_without_replace_change_nothing(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    add_category(engine, login, "Моё")
    with pytest.raises(tool.LegacyError, match="уже есть категории"):
        run_import(engine, path, login)
    assert rows(engine, "SELECT name, deleted_at FROM categories WHERE user_id = :u", u=user_id(engine, login)) == [("Моё", None)]


def test_bad_data_midway_leaves_no_trace(engine: Engine, tmp_path: Path) -> None:
    path, shop = legacy_db(tmp_path, bad_price=True)
    login = owner(engine)
    add_category(engine, login, "Моё")
    with pytest.raises(tool.LegacyError, match="целых копейках"):
        run_import(engine, path, login, replace=True)
    assert rows(engine, "SELECT name, deleted_at FROM categories WHERE user_id = :u", u=user_id(engine, login)) == [("Моё", None)]
    assert rows(engine, "SELECT count(*) FROM stores WHERE canonical_host = :s", s=shop) == [(0,)]


def test_second_import_is_refused(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    run_import(engine, path, login)
    with pytest.raises(tool.LegacyError, match="уже выполнялся"):
        run_import(engine, path, login, replace=True)
    assert rows(engine, "SELECT count(*) FROM price_observations") == [(7,)]


def test_unknown_login_is_refused(engine: Engine, tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    with pytest.raises(tool.LegacyError, match="нет пользователя"):
        run_import(engine, path, f"nobody{uuid.uuid4().hex[:6]}")


def test_database_in_wal_mode_is_refused_with_hint(tmp_path: Path) -> None:
    path, _ = legacy_db(tmp_path)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.close()
    with pytest.raises(tool.LegacyError, match="journal_mode=DELETE"):
        tool.read_legacy(path)


def test_missing_legacy_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(tool.LegacyError, match="нет файла"):
        tool.read_legacy(tmp_path / "absent.db")


def test_command_reports_refusal_and_exit_code(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path, _ = legacy_db(tmp_path)
    monkeypatch.setenv("PRICEWATCH_DATABASE_URL", database_url)
    monkeypatch.setenv("PRICEWATCH_SECRET_KEY", "k" * 48)
    monkeypatch.setenv("PRICEWATCH_ENV", "test")
    monkeypatch.setenv("PRICEWATCH_PUBLIC_ORIGIN", "http://localhost:8000")
    assert tool.main([str(path), "--login", f"nobody{uuid.uuid4().hex[:6]}"]) == 1
    assert "Перенос отменён, в новой базе ничего не изменилось: нет пользователя" in capsys.readouterr().err


def test_command_imports_and_prints_summary(
    engine: Engine, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path, _ = legacy_db(tmp_path)
    login = owner(engine)
    monkeypatch.setenv("PRICEWATCH_DATABASE_URL", database_url)
    monkeypatch.setenv("PRICEWATCH_SECRET_KEY", "k" * 48)
    monkeypatch.setenv("PRICEWATCH_ENV", "test")
    monkeypatch.setenv("PRICEWATCH_PUBLIC_ORIGIN", "http://localhost:8000")
    assert tool.main([str(path), "--login", login]) == 0
    assert "Перенесено: категорий 1, групп 1, товаров 3, магазинов 2, адресов ссылок 4, запусков 3, проверок 7, решений 1." in capsys.readouterr().out
