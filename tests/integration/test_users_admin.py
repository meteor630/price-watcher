"""Пользователи у администратора на настоящей базе: блокировка с отзывом входа, разблокировка с возвратом данных,
снятие прав администратора, последний администратор, приглашения, журнал."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, World

from pricewatch.application.accounts import create_invite
from pricewatch.application.retention import delete_blocked_users_data, purge_deleted
from pricewatch.application.users_admin import block_user, delete_invites, demote_admin, unblock_user, users_page
from pricewatch.domain.accounts import Role
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"users-admin-key-" + b"k" * 30


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


def signed_in(world: World) -> None:
    world.one("INSERT INTO web_sessions (user_id, token_hash, created_at, expires_at) VALUES (:u, md5(random()::text) || md5(random()::text), :t, :e) RETURNING id",
              u=world.user.id, t=NOW - DAY, e=NOW + 29 * DAY)
    world.one("INSERT INTO devices (user_id, name, created_at) VALUES (:u, 'iPhone', :t) RETURNING id", u=world.user.id, t=NOW - DAY)


def active_login(world: World) -> tuple[int, int]:
    sessions = world.one("SELECT count(*) FROM web_sessions WHERE user_id = :u AND revoked_at IS NULL", u=world.user.id)
    devices = world.one("SELECT count(*) FROM devices WHERE user_id = :u AND revoked_at IS NULL", u=world.user.id)
    return sessions, devices


def visible_items(world: World) -> list[str]:
    with world.engine.connect() as db:
        return [row[0] for row in db.execute(text("SELECT name FROM items WHERE user_id = :u AND deleted_at IS NULL ORDER BY id"), {"u": world.user.id})]


def test_page_lists_users_with_last_login(engine: Engine) -> None:
    admin, user = World(engine, Role.ADMIN), World(engine)
    signed_in(user)

    page = users_page(uow(engine), admin.user)

    rows = {row.user_id: row for row in page.users}
    assert (rows[user.user.id].role, rows[user.user.id].last_login_at, rows[admin.user.id].last_login_at) == (Role.USER, NOW - DAY, None)
    assert error(lambda: users_page(uow(engine), user.user))[0] is ErrorCode.FORBIDDEN


def test_block_revokes_login_and_unblock_before_deletion_keeps_data(engine: Engine) -> None:
    admin, user = World(engine, Role.ADMIN), World(engine)
    user.item(user.category("Дом"), "Лампа")
    signed_in(user)

    block_user(uow(engine), admin.user, user.user.id, NOW)

    assert active_login(user) == (0, 0)
    assert user.one("SELECT disabled_at FROM users WHERE id = :u", u=user.user.id) == NOW
    assert error(lambda: block_user(uow(engine), admin.user, user.user.id, NOW)) == (ErrorCode.VALIDATION_ERROR, {"fields": {"user": "already_blocked"}})
    result = unblock_user(uow(engine), admin.user, user.user.id, NOW + 3 * DAY)
    assert (result.restored, result.too_late) == (0, False) and visible_items(user) == ["Лампа"]
    assert user.one("SELECT count(*) FROM users WHERE id = :u AND disabled_at IS NULL", u=user.user.id) == 1


def test_unblock_after_deletion_returns_what_block_deleted(engine: Engine) -> None:
    admin, user = World(engine, Role.ADMIN), World(engine)
    home = user.category("Дом")
    user.item(home, "Лампа")
    own_deleted = user.item(home, "Удалил сам")
    user.one("UPDATE items SET deleted_at = :t WHERE id = :i RETURNING id", t=NOW - DAY, i=own_deleted)

    block_user(uow(engine), admin.user, user.user.id, NOW)
    delete_blocked_users_data(uow(engine), NOW + 7 * DAY)
    assert visible_items(user) == []

    result = unblock_user(uow(engine), admin.user, user.user.id, NOW + 10 * DAY)

    assert (result.restored, result.too_late) == (2, False)  # категория и товар; удалённое самим не вернулось
    assert visible_items(user) == ["Лампа"]


def test_unblock_after_purge_is_too_late_and_old_deletion_is_ignored(engine: Engine) -> None:
    admin, late, again = World(engine, Role.ADMIN), World(engine), World(engine)
    for world in (late, again):
        world.item(world.category("Дом"), "Лампа")
        block_user(uow(engine), admin.user, world.user.id, NOW)
    delete_blocked_users_data(uow(engine), NOW + 7 * DAY)
    purge_deleted(uow(engine), NOW + 38 * DAY)

    result = unblock_user(uow(engine), admin.user, late.user.id, NOW + 40 * DAY)
    assert (result.restored, result.too_late) == (0, True)

    # Повторная блокировка: удаление прошлой блокировки к новой не относится.
    unblock_user(uow(engine), admin.user, again.user.id, NOW + 40 * DAY)
    again.item(again.category("Новая"), "Новый")
    block_user(uow(engine), admin.user, again.user.id, NOW + 41 * DAY)
    assert unblock_user(uow(engine), admin.user, again.user.id, NOW + 42 * DAY).restored == 0
    assert visible_items(again) == ["Новый"]


def test_last_admin_and_self_are_protected(engine: Engine) -> None:
    first, second = World(engine, Role.ADMIN), World(engine, Role.ADMIN)

    assert error(lambda: block_user(uow(engine), first.user, first.user.id, NOW)) == (ErrorCode.VALIDATION_ERROR, {"fields": {"user": "self"}})
    assert error(lambda: demote_admin(uow(engine), first.user, first.user.id, NOW))[1] == {"fields": {"user": "self"}}
    demote_admin(uow(engine), first.user, second.user.id, NOW)  # второй — уже обычный пользователь
    assert error(lambda: demote_admin(uow(engine), first.user, second.user.id, NOW))[1] == {"fields": {"user": "not_admin"}}
    # Второй теперь не администратор и ничего не может; первый — последний, заблокировать его некому.
    assert error(lambda: block_user(uow(engine), second.user, first.user.id, NOW))[0] is ErrorCode.FORBIDDEN


def test_blocking_other_admin_keeps_at_least_one(engine: Engine) -> None:
    first, second = World(engine, Role.ADMIN), World(engine, Role.ADMIN)
    block_user(uow(engine), first.user, second.user.id, NOW)

    with engine.begin() as db:
        rows = db.execute(text("SELECT action FROM audit_events WHERE actor_id = :u ORDER BY id"), {"u": first.user.id}).scalars().all()
    assert rows == ["user_blocked"]
    # Заблокированный администратор больше не действует: оставшийся один — защищён.
    third = World(engine, Role.ADMIN)
    demote_admin(uow(engine), third.user, first.user.id, NOW)
    assert error(lambda: block_user(uow(engine), first.user, third.user.id, NOW))[0] is ErrorCode.FORBIDDEN


def test_invites_listed_and_deleted_selectively(engine: Engine) -> None:
    admin, user = World(engine, Role.ADMIN), World(engine)
    first = create_invite(uow(engine), KEY, admin.user.id, Role.USER, NOW).invite.id
    second = create_invite(uow(engine), KEY, admin.user.id, Role.ADMIN, NOW + timedelta(minutes=1)).invite.id
    used = create_invite(uow(engine), KEY, admin.user.id, Role.USER, NOW).invite.id
    admin.one("UPDATE invites SET used_by = :u, used_at = :t WHERE id = :i RETURNING id", u=user.user.id, t=NOW, i=used)

    assert [(invite.invite_id, invite.role) for invite in users_page(uow(engine), admin.user).invites] == [(second, Role.ADMIN), (first, Role.USER)]
    assert delete_invites(uow(engine), admin.user, [first, used, 10**12], NOW) == [first]
    assert [invite.invite_id for invite in users_page(uow(engine), admin.user).invites] == [second]
    assert error(lambda: delete_invites(uow(engine), admin.user, [], NOW))[1] == {"fields": {"invites": "none_selected"}}
    assert error(lambda: delete_invites(uow(engine), user.user, [second], NOW))[0] is ErrorCode.FORBIDDEN
    assert admin.one("SELECT count(*) FROM audit_events WHERE action = 'invite_created'") == 3
