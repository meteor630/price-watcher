"""SqlUserRepository: создание, уникальность логина без учёта регистра, выборка."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from pricewatch.domain.accounts import Role
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

HASH = "$argon2id$v=19$m=8192,t=1,p=4$c2FsdA$aGFzaA"


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def uow(engine: Engine) -> Iterator[SqlUnitOfWork]:
    with SqlUnitOfWork(engine) as work:
        yield work


def login() -> str:
    return f"u{uuid.uuid4().hex[:10]}"


def test_create_and_get(uow: SqlUnitOfWork) -> None:
    name = login()

    created = uow.users.create(name, HASH, Role.ADMIN, "Админ")

    assert (created.login, created.role, created.display_name, created.disabled) == (name, Role.ADMIN, "Админ", False)
    assert uow.users.get(created.id) == created


def test_login_taken_ignoring_case(uow: SqlUnitOfWork) -> None:
    name = login()
    uow.users.create(name, HASH, Role.USER, "Первый")

    with pytest.raises(PriceWatcherError) as caught:
        uow.users.create(name.upper(), HASH, Role.USER, "Второй")

    assert caught.value.code is ErrorCode.LOGIN_TAKEN
    uow.users.create(login(), HASH, Role.USER, "Третий")  # транзакция осталась рабочей


def test_get_unknown_user(uow: SqlUnitOfWork) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        uow.users.get(10**12)

    assert caught.value.code is ErrorCode.NOT_FOUND


def test_disabled_flag(uow: SqlUnitOfWork) -> None:
    user = uow.users.create(login(), HASH, Role.USER, "Будет отключён")
    uow.users.connection.execute(text("UPDATE users SET disabled_at = now() WHERE id = :id"), {"id": user.id})

    assert uow.users.get(user.id).disabled


def test_repository_matches_protocol() -> None:
    import inspect

    from pricewatch.application.ports import UserRepository
    from pricewatch.persistence.user_repository import SqlUserRepository

    for name in [n for n in vars(UserRepository) if not n.startswith("_")]:
        expected = list(inspect.signature(getattr(UserRepository, name)).parameters)
        assert list(inspect.signature(getattr(SqlUserRepository, name)).parameters) == expected, name
