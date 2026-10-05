"""Ограничения схемы срабатывают в самой базе, а не только в коде.

Каждый тест работает в транзакции, которая откатывается; нарушение проверяется
по имени ограничения из ошибки PostgreSQL.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DataError, IntegrityError


@pytest.fixture
def db(migrated_database_url: str) -> Iterator[Connection]:
    engine = create_engine(migrated_database_url)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def run(db: Connection, sql: str, **params: object) -> int | None:
    result = db.execute(text(sql), params)
    return result.scalar() if result.returns_rows else None


def violated(db: Connection, sql: str, **params: object) -> str:
    """Выполнить запрос, который должен нарушить ограничение; вернуть имя ограничения."""
    # Ошибка сначала откатывает точку сохранения, затем её ловит pytest.raises.
    with pytest.raises(IntegrityError) as caught, db.begin_nested():
        run(db, sql, **params)
    return str(caught.value.orig.diag.constraint_name)  # type: ignore[union-attr]


def user(db: Connection, login: str, role: str = "user") -> int:
    return run(
        db,
        "INSERT INTO users (login, password_hash, role, display_name) VALUES (:login, 'h', :role, :login) RETURNING id",
        login=login,
        role=role,
    )  # type: ignore[return-value]


def category(db: Connection, user_id: int, name: str, position: int = 0) -> int:
    return run(
        db,
        "INSERT INTO categories (user_id, name, name_normalized, position) "
        "VALUES (:u, :n, lower(:n), :p) RETURNING id",
        u=user_id,
        n=name,
        p=position,
    )  # type: ignore[return-value]


def group(db: Connection, user_id: int, category_id: int, name: str) -> int:
    return run(
        db,
        "INSERT INTO groups (user_id, category_id, name, name_normalized, position) "
        "VALUES (:u, :c, :n, lower(:n), 0) RETURNING id",
        u=user_id,
        c=category_id,
        n=name,
    )  # type: ignore[return-value]


ITEM_SQL = (
    "INSERT INTO items (user_id, category_id, group_id, name, quantity, position) "
    "VALUES (:u, :c, :g, :n, :q, 0) RETURNING id"
)


def test_category_name_unique_per_user_among_active(db: Connection) -> None:
    alice, bob = user(db, "alice"), user(db, "bob")
    first = category(db, alice, "Компьютер")

    assert violated(
        db, "INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, 'компьютер', 'компьютер', 1)", u=alice
    ) == "uq_categories_user_id_name_active"
    category(db, bob, "Компьютер")

    run(db, "UPDATE categories SET deleted_at = now() WHERE id = :id", id=first)
    category(db, alice, "Компьютер")


def test_group_only_in_own_category(db: Connection) -> None:
    alice, bob = user(db, "alice"), user(db, "bob")
    bobs = category(db, bob, "Чужая")

    assert violated(
        db,
        "INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, 'Г', 'г', 0)",
        u=alice,
        c=bobs,
    ) == "fk_groups_category_id_user_id"


def test_group_name_unique_within_category(db: Connection) -> None:
    alice = user(db, "alice")
    first, second = category(db, alice, "А"), category(db, alice, "Б", 1)
    group(db, alice, first, "Мониторы")

    assert violated(
        db,
        "INSERT INTO groups (user_id, category_id, name, name_normalized, position) "
        "VALUES (:u, :c, 'МОНИТОРЫ', 'мониторы', 1)",
        u=alice,
        c=first,
    ) == "uq_groups_category_id_name_active"
    group(db, alice, second, "Мониторы")


def test_item_only_in_own_category(db: Connection) -> None:
    alice, bob = user(db, "alice"), user(db, "bob")
    bobs = category(db, bob, "Чужая")

    assert violated(db, ITEM_SQL, u=alice, c=bobs, g=None, n="Товар", q=1) == "fk_items_category_id_user_id"


def test_item_group_must_belong_to_item_category(db: Connection) -> None:
    alice = user(db, "alice")
    first, second = category(db, alice, "А"), category(db, alice, "Б", 1)
    other_group = group(db, alice, second, "Группа")

    assert violated(db, ITEM_SQL, u=alice, c=first, g=other_group, n="Товар", q=1) == (
        "fk_items_group_id_category_id_user_id"
    )
    run(db, ITEM_SQL, u=alice, c=first, g=None, n="Без группы", q=1)
    run(db, ITEM_SQL, u=alice, c=second, g=other_group, n="В группе", q=1)


@pytest.mark.parametrize("quantity", [0, 1000, -1])
def test_item_quantity_out_of_range(db: Connection, quantity: int) -> None:
    alice = user(db, "alice")
    cat = category(db, alice, "А")

    assert violated(db, ITEM_SQL, u=alice, c=cat, g=None, n="Товар", q=quantity) == "ck_items_quantity"


@pytest.mark.parametrize("quantity", [1, 999])
def test_item_quantity_bounds_accepted(db: Connection, quantity: int) -> None:
    alice = user(db, "alice")

    run(db, ITEM_SQL, u=alice, c=category(db, alice, "А"), g=None, n="Товар", q=quantity)


@pytest.mark.parametrize(
    ("name", "constraint"),
    [("", "ck_categories_name_length"), ("я" * 101, "ck_categories_name_length")],
)
def test_category_name_length(db: Connection, name: str, constraint: str) -> None:
    alice = user(db, "alice")

    assert violated(
        db,
        "INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, :n, 'x', 0)",
        u=alice,
        n=name,
    ) == constraint
    category(db, alice, "я" * 100)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [("position", -1, "ck_categories_position"), ("version", 0, "ck_categories_version")],
)
def test_category_position_and_version(db: Connection, column: str, value: int, constraint: str) -> None:
    alice = user(db, "alice")
    cat = category(db, alice, "А")

    assert violated(db, f"UPDATE categories SET {column} = :v WHERE id = :id", v=value, id=cat) == constraint


def test_item_name_and_full_name_length(db: Connection) -> None:
    alice = user(db, "alice")
    cat = category(db, alice, "А")

    assert violated(db, ITEM_SQL, u=alice, c=cat, g=None, n="я" * 201, q=1) == "ck_items_name_length"
    item = run(db, ITEM_SQL, u=alice, c=cat, g=None, n="я" * 200, q=1)
    assert violated(db, "UPDATE items SET full_name = :f WHERE id = :id", f="", id=item) == "ck_items_full_name_length"
    run(db, "UPDATE items SET full_name = :f WHERE id = :id", f="я" * 500, id=item)


def test_role_must_be_known(db: Connection) -> None:
    assert violated(
        db, "INSERT INTO users (login, password_hash, role, display_name) VALUES ('root', 'h', 'root', 'R')"
    ) == "ck_users_role"
    user(db, "admin1", role="admin")


def test_login_unique_ignoring_case(db: Connection) -> None:
    user(db, "Alice")

    assert violated(
        db, "INSERT INTO users (login, password_hash, role, display_name) VALUES ('alice', 'h', 'user', 'A')"
    ) == "uq_users_login_lower"


def test_telegram_chat_bound_to_one_user(db: Connection) -> None:
    alice, bob = user(db, "alice"), user(db, "bob")
    run(db, "UPDATE users SET telegram_chat_id = 42 WHERE id = :id", id=alice)

    assert violated(db, "UPDATE users SET telegram_chat_id = 42 WHERE id = :id", id=bob) == "uq_users_telegram_chat_id"


def test_defaults_for_new_records(db: Connection) -> None:
    alice = user(db, "alice")
    cat = category(db, alice, "А")
    item = run(db, "INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, 'Т', 0) RETURNING id", u=alice, c=cat)

    row = db.execute(text("SELECT quantity, in_total, version, deleted_at FROM items WHERE id = :id"), {"id": item}).one()
    assert tuple(row) == (1, True, 1, None)
    assert run(db, "SELECT categories_order_version FROM users WHERE id = :id", id=alice) == 1


# --- таблицы входа (этап 3) --------------------------------------------------

HASH = "a" * 64
OTHER_HASH = "b" * 64


@pytest.mark.parametrize(
    ("table", "column", "insert"),
    [
        ("invites", "code_hash", "INSERT INTO invites (code_hash, role, created_by, expires_at) VALUES (:h, 'user', :u, now() + interval '7 days')"),
        ("web_sessions", "token_hash", "INSERT INTO web_sessions (user_id, token_hash, expires_at) VALUES (:u, :h, now() + interval '30 days')"),
        ("pairing_codes", "code_hash", "INSERT INTO pairing_codes (user_id, code_hash, expires_at) VALUES (:u, :h, now() + interval '5 minutes')"),
    ],
)
@pytest.mark.parametrize(
    "bad_hash",
    [
        "x" * 43,  # токен в открытом виде: base64url, 43 символа
        "A" * 64,  # заглавные — не наш формат
        "a" * 63,
        "",
    ],
)
def test_user_hash_columns_accept_only_hmac_hex(
    db: Connection, table: str, column: str, insert: str, bad_hash: str
) -> None:
    alice = user(db, "alice")

    assert violated(db, insert, u=alice, h=bad_hash) == f"ck_{table}_{column}_format"
    run(db, insert, u=alice, h=HASH)
    assert violated(db, insert, u=alice, h=HASH) == f"uq_{table}_{column}"


def device(db: Connection, user_id: int, name: str = "iPhone") -> int:
    return run(db, "INSERT INTO devices (user_id, name) VALUES (:u, :n) RETURNING id", u=user_id, n=name)  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("table", "insert"),
    [
        ("access_tokens", "INSERT INTO access_tokens (device_id, token_hash, expires_at) VALUES (:d, :h, now() + interval '15 minutes')"),
        ("refresh_tokens", "INSERT INTO refresh_tokens (device_id, token_hash, expires_at) VALUES (:d, :h, now() + interval '60 days')"),
    ],
)
def test_device_token_columns_accept_only_hmac_hex(db: Connection, table: str, insert: str) -> None:
    dev = device(db, user(db, "alice"))

    assert violated(db, insert, d=dev, h="x" * 43) == f"ck_{table}_token_hash_format"
    run(db, insert, d=dev, h=HASH)
    assert violated(db, insert, d=dev, h=HASH) == f"uq_{table}_token_hash"


def test_invite_used_by_and_used_at_go_together(db: Connection) -> None:
    admin, newcomer = user(db, "admin1", role="admin"), user(db, "newcomer")
    invite = run(
        db,
        "INSERT INTO invites (code_hash, role, created_by, expires_at) VALUES (:h, 'user', :a, now() + interval '7 days') RETURNING id",
        h=HASH,
        a=admin,
    )

    assert violated(db, "UPDATE invites SET used_by = :n WHERE id = :i", n=newcomer, i=invite) == "ck_invites_used_together"
    assert violated(db, "UPDATE invites SET used_at = now() WHERE id = :i", i=invite) == "ck_invites_used_together"
    run(db, "UPDATE invites SET used_by = :n, used_at = now() WHERE id = :i", n=newcomer, i=invite)


def test_invite_role_and_expiry(db: Connection) -> None:
    admin = user(db, "admin1", role="admin")

    assert violated(
        db,
        "INSERT INTO invites (code_hash, role, created_by, expires_at) VALUES (:h, 'root', :a, now() + interval '1 day')",
        h=HASH,
        a=admin,
    ) == "ck_invites_role"
    assert violated(
        db,
        "INSERT INTO invites (code_hash, role, created_by, expires_at) VALUES (:h, 'user', :a, now() - interval '1 second')",
        h=OTHER_HASH,
        a=admin,
    ) == "ck_invites_expires_after_created"


def test_one_invite_per_registered_user(db: Connection) -> None:
    admin, newcomer = user(db, "admin1", role="admin"), user(db, "newcomer")
    sql = (
        "INSERT INTO invites (code_hash, role, created_by, expires_at, used_by, used_at) "
        "VALUES (:h, 'user', :a, now() + interval '7 days', :n, now())"
    )
    run(db, sql, h=HASH, a=admin, n=newcomer)

    assert violated(db, sql, h=OTHER_HASH, a=admin, n=newcomer) == "uq_invites_used_by"


@pytest.mark.parametrize("name", ["", "я" * 81])
def test_device_name_length(db: Connection, name: str) -> None:
    alice = user(db, "alice")

    assert violated(db, "INSERT INTO devices (user_id, name) VALUES (:u, :n)", u=alice, n=name) == "ck_devices_name_length"
    device(db, alice, "я" * 80)


def test_auth_failure_kind_and_subject(db: Connection) -> None:
    insert = "INSERT INTO auth_failures (kind, subject, ip) VALUES (:k, :s, CAST(:ip AS inet))"
    run(db, insert, k="login", s="alice", ip="203.0.113.7")
    run(db, insert, k="pairing", s=None, ip="2001:db8::1")

    assert violated(db, insert, k="password", s="alice", ip="203.0.113.7") == "ck_auth_failures_kind"
    assert violated(db, insert, k="login", s="я" * 65, ip=None) == "ck_auth_failures_subject_length"
    with pytest.raises(DataError), db.begin_nested():
        run(db, insert, k="login", s="alice", ip="not-an-ip")


def test_tokens_belong_to_existing_device(db: Connection) -> None:
    assert violated(
        db,
        "INSERT INTO access_tokens (device_id, token_hash, expires_at) VALUES (:d, :h, now() + interval '15 minutes')",
        d=10**12,
        h=HASH,
    ) == "fk_access_tokens_device_id"
