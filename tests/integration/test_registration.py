"""Приглашения и регистрация: только администратор, одноразовость, срок, неотличимые отказы, гонка."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from concurrency_helpers import overlapped
from sqlalchemy import Engine, text

from pricewatch.application.accounts import create_invite, register
from pricewatch.application.passwords import PasswordHasher
from pricewatch.domain.accounts import INVITE_LIFETIME, Role
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, keyed_hash
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"registration-test-key-" + b"k" * 16
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PASSWORD = "надёжный пароль 123"


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher(time_cost=1, memory_cost=8 * 1024)


def add_user(engine: Engine, role: str = "admin", disabled: bool = False) -> int:
    with engine.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO users (login, password_hash, role, display_name, disabled_at) "
                "VALUES (:l, 'h', :r, 'U', CASE WHEN :d THEN now() END) RETURNING id"
            ),
            {"l": f"a{uuid.uuid4().hex[:10]}", "r": role, "d": disabled},
        ).scalar_one()


@pytest.fixture
def admin(engine: Engine) -> int:
    return add_user(engine)


def invite(engine: Engine, admin_id: int, role: Role = Role.USER, now: datetime = NOW) -> str:
    return create_invite(SqlUnitOfWork(engine), KEY, admin_id, role, now).code


def sign_up(engine: Engine, hasher: PasswordHasher, code: str, login: str | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
    values = {"password": PASSWORD, "display_name": "Новичок", "now": NOW} | kwargs
    return register(SqlUnitOfWork(engine), hasher, KEY, code, login or f"n{uuid.uuid4().hex[:10]}", **values)  # type: ignore[arg-type]


def code_of(action) -> ErrorCode:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code


def invite_row(engine: Engine, code: str) -> tuple:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                text("SELECT role, created_by, expires_at, used_by, used_at FROM invites WHERE code_hash = :h"),
                {"h": keyed_hash(KEY, Purpose.INVITE, code)},
            ).one()
        )


def test_admin_creates_invite_shown_once(engine: Engine, admin: int) -> None:
    issued = create_invite(SqlUnitOfWork(engine), KEY, admin, Role.USER, NOW)

    assert len(issued.code) == 43
    assert issued.invite.expires_at == NOW + INVITE_LIFETIME
    role, created_by, expires_at, used_by, used_at = invite_row(engine, issued.code)
    assert (role, created_by, expires_at, used_by, used_at) == ("user", admin, NOW + timedelta(days=7), None, None)
    with engine.connect() as connection:
        stored = connection.execute(text("SELECT count(*) FROM invites WHERE code_hash = :c"), {"c": issued.code}).scalar_one()
    assert stored == 0  # сам код в базе не хранится


@pytest.mark.parametrize(("role", "disabled", "expected"), [("user", False, ErrorCode.FORBIDDEN), ("admin", True, ErrorCode.FORBIDDEN)])
def test_only_active_admin_creates_invites(engine: Engine, role: str, disabled: bool, expected: ErrorCode) -> None:
    actor = add_user(engine, role=role, disabled=disabled)

    assert code_of(lambda: create_invite(SqlUnitOfWork(engine), KEY, actor, Role.USER, NOW)) is expected


def test_unknown_actor_cannot_create_invite(engine: Engine) -> None:
    assert code_of(lambda: create_invite(SqlUnitOfWork(engine), KEY, 10**12, Role.USER, NOW)) is ErrorCode.NOT_FOUND


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
def test_register_gets_invite_role_and_uses_invite(engine: Engine, hasher: PasswordHasher, admin: int, role: Role) -> None:
    code = invite(engine, admin, role)

    user = sign_up(engine, hasher, code, display_name="  Иван  ")

    assert (user.role, user.display_name, user.disabled) == (role, "Иван", False)
    _, _, _, used_by, used_at = invite_row(engine, code)
    assert (used_by, used_at) == (user.id, NOW)
    with engine.connect() as connection:
        password_hash = connection.execute(text("SELECT password_hash FROM users WHERE id = :id"), {"id": user.id}).scalar_one()
    assert hasher.verify(password_hash, PASSWORD)


def test_used_expired_and_unknown_invites_are_indistinguishable(engine: Engine, hasher: PasswordHasher, admin: int) -> None:
    used = invite(engine, admin)
    sign_up(engine, hasher, used)
    expired = invite(engine, admin, now=NOW - INVITE_LIFETIME - timedelta(seconds=1))

    errors = []
    for code in (used, expired, "выдуманный-код", "", "x" * 43):
        with pytest.raises(PriceWatcherError) as caught:
            sign_up(engine, hasher, code)
        errors.append((caught.value.code, caught.value.safe_message, dict(caught.value.details)))

    assert errors == [(ErrorCode.INVITE_INVALID, SAFE_MESSAGES[ErrorCode.INVITE_INVALID], {})] * 5


def test_invite_expiry_boundary(engine: Engine, hasher: PasswordHasher, admin: int) -> None:
    code = invite(engine, admin)

    assert code_of(lambda: sign_up(engine, hasher, code, now=NOW + INVITE_LIFETIME)) is ErrorCode.INVITE_INVALID
    assert sign_up(engine, hasher, code, now=NOW + INVITE_LIFETIME - timedelta(microseconds=1)).role is Role.USER


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [({"password": "короткий"}, "password"), ({"display_name": "   "}, "display_name"), ({"login": "иван"}, "login")],
)
def test_invalid_fields_do_not_spend_invite(
    engine: Engine, hasher: PasswordHasher, admin: int, kwargs: dict, field: str
) -> None:
    code = invite(engine, admin)

    with pytest.raises(PriceWatcherError) as caught:
        sign_up(engine, hasher, code, **kwargs)

    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert field in caught.value.details["fields"]  # type: ignore[operator]
    assert invite_row(engine, code)[3] is None
    sign_up(engine, hasher, code)  # приглашение осталось рабочим


def test_taken_login_does_not_spend_invite(engine: Engine, hasher: PasswordHasher, admin: int) -> None:
    login = f"t{uuid.uuid4().hex[:10]}"
    sign_up(engine, hasher, invite(engine, admin), login=login)
    code = invite(engine, admin)

    assert code_of(lambda: sign_up(engine, hasher, code, login=login.upper())) is ErrorCode.LOGIN_TAKEN
    assert invite_row(engine, code)[3] is None


def test_claimed_invite_blocks_second_registration(engine: Engine, admin: int) -> None:
    # register() фиксирует транзакцию сам, а здесь первая транзакция должна держаться открытой.
    # Поэтому шаги register() повторены на тех же методах репозитория: проверяется гарантия
    # claim() (FOR UPDATE), на которой register() стоит.
    code = invite(engine, admin)
    code_hash = keyed_hash(KEY, Purpose.INVITE, code)

    def claim_and_register(login: str):  # type: ignore[no-untyped-def]
        def action(work: SqlUnitOfWork) -> object:
            claimed = work.invites.claim(code_hash, NOW)
            if claimed is None:
                raise PriceWatcherError(ErrorCode.INVITE_INVALID)
            user = work.users.create(login, "h", claimed.role, "Гонка")
            work.invites.mark_used(claimed.id, user.id, NOW)
            return user

        return action

    first, second = overlapped(
        engine, claim_and_register(f"r{uuid.uuid4().hex[:10]}"), claim_and_register(f"r{uuid.uuid4().hex[:10]}")
    )

    assert first.value is not None
    assert second.error is ErrorCode.INVITE_INVALID
    assert invite_row(engine, code)[3] == first.value.id  # type: ignore[attr-defined]


def test_invite_times_come_from_one_clock(engine: Engine, admin: int) -> None:
    # Регрессия: created_at брался из часов базы, а expires_at — из «сейчас» сценария.
    # При расхождении часов запись нарушала expires_after_created.
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)

    code = invite(engine, admin, now=long_ago)

    with engine.connect() as connection:
        created_at, expires_at = connection.execute(
            text("SELECT created_at, expires_at FROM invites WHERE code_hash = :h"),
            {"h": keyed_hash(KEY, Purpose.INVITE, code)},
        ).one()
    assert (created_at, expires_at) == (long_ago, long_ago + INVITE_LIFETIME)


def test_invite_repository_matches_protocol() -> None:
    import inspect

    from pricewatch.application.ports import InviteRepository
    from pricewatch.persistence.invite_repository import SqlInviteRepository

    for name in [n for n in vars(InviteRepository) if not n.startswith("_")]:
        expected = list(inspect.signature(getattr(InviteRepository, name)).parameters)
        assert list(inspect.signature(getattr(SqlInviteRepository, name)).parameters) == expected, name
