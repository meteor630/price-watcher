"""Вход в приложение: код связки, устройство, ротация refresh-токенов с окном 60 секунд."""

import itertools
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.app_auth import (
    authenticate_access,
    issue_pairing_code,
    logout_device,
    pair,
    refresh,
)
from pricewatch.domain.accounts import (
    ACCESS_TOKEN_LIFETIME,
    PAIRING_CODE_LIFETIME,
    REFRESH_REPLAY_WINDOW,
    REFRESH_TOKEN_LIFETIME,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, derive_token, keyed_hash
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"app-auth-test-key-" + b"k" * 16
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


@pytest.fixture
def user_id(engine: Engine) -> int:
    with engine.begin() as connection:
        return connection.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"),
            {"l": f"p{uuid.uuid4().hex[:10]}"},
        ).scalar_one()


_IP_COUNTER = itertools.count(1)


def ip() -> str:
    # Уникальный адрес: случайный из 250 изредка совпадал с уже заблокированным.
    return f"2001:db8:b::{next(_IP_COUNTER):x}"


def error_of(action) -> PriceWatcherError:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value


def paired(engine: Engine, user_id: int, now: datetime = NOW):  # type: ignore[no-untyped-def]
    code = issue_pairing_code(uow(engine), KEY, user_id, now).code
    return pair(uow(engine), KEY, code, "iPhone Владимира", ip(), now)


def disable(engine: Engine, user_id: int) -> None:
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET disabled_at = now() WHERE id = :id"), {"id": user_id})


# --- код связки ----------------------------------------------------------------


def test_pairing_code_stored_as_hash_for_five_minutes(engine: Engine, user_id: int) -> None:
    issued = issue_pairing_code(uow(engine), KEY, user_id, NOW)

    assert issued.expires_at == NOW + PAIRING_CODE_LIFETIME
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT code_hash, created_at, expires_at FROM pairing_codes WHERE code_hash = :h"),
            {"h": keyed_hash(KEY, Purpose.PAIRING_CODE, issued.code)},
        ).one()
    assert (row.created_at, row.expires_at) == (NOW, NOW + PAIRING_CODE_LIFETIME)
    assert issued.code not in row.code_hash


def test_disabled_user_gets_no_code(engine: Engine, user_id: int) -> None:
    disable(engine, user_id)

    assert error_of(lambda: issue_pairing_code(uow(engine), KEY, user_id, NOW)).code is ErrorCode.FORBIDDEN


def test_pair_creates_device_and_tokens(engine: Engine, user_id: int) -> None:
    device, tokens = paired(engine, user_id)

    assert (device.user_id, device.name, device.created_at) == (user_id, "iPhone Владимира", NOW)
    assert (tokens.access_expires_at, tokens.refresh_expires_at) == (NOW + ACCESS_TOKEN_LIFETIME, NOW + REFRESH_TOKEN_LIFETIME)
    assert authenticate_access(uow(engine), KEY, tokens.access_token, NOW).user.id == user_id


def test_pairing_code_is_one_time(engine: Engine, user_id: int) -> None:
    code = issue_pairing_code(uow(engine), KEY, user_id, NOW).code
    pair(uow(engine), KEY, code, "Первое", ip(), NOW)

    assert error_of(lambda: pair(uow(engine), KEY, code, "Второе", ip(), NOW)).code is ErrorCode.PAIRING_CODE_INVALID


def test_pairing_code_expiry_boundary(engine: Engine, user_id: int) -> None:
    code = issue_pairing_code(uow(engine), KEY, user_id, NOW).code

    assert error_of(
        lambda: pair(uow(engine), KEY, code, "Поздно", ip(), NOW + PAIRING_CODE_LIFETIME)
    ).code is ErrorCode.PAIRING_CODE_EXPIRED
    pair(uow(engine), KEY, code, "Вовремя", ip(), NOW + PAIRING_CODE_LIFETIME - timedelta(microseconds=1))


def test_unknown_code_and_disabled_owner(engine: Engine, user_id: int) -> None:
    code = issue_pairing_code(uow(engine), KEY, user_id, NOW).code
    disable(engine, user_id)

    assert error_of(lambda: pair(uow(engine), KEY, "ABCDEFGH", "X", ip(), NOW)).code is ErrorCode.PAIRING_CODE_INVALID
    assert error_of(lambda: pair(uow(engine), KEY, code, "X", ip(), NOW)).code is ErrorCode.PAIRING_CODE_INVALID


def test_device_name_validated_before_code_is_spent(engine: Engine, user_id: int) -> None:
    code = issue_pairing_code(uow(engine), KEY, user_id, NOW).code

    assert error_of(lambda: pair(uow(engine), KEY, code, "   ", ip(), NOW)).code is ErrorCode.VALIDATION_ERROR
    pair(uow(engine), KEY, code, "Нормальное имя", ip(), NOW)


def test_pairing_attempts_limited_per_ip_even_for_right_code(engine: Engine, user_id: int) -> None:
    attacker = ip()
    for _ in range(10):
        error_of(lambda: pair(uow(engine), KEY, "ZZZZZZZZ", "X", attacker, NOW))
    code = issue_pairing_code(uow(engine), KEY, user_id, NOW).code

    blocked = error_of(lambda: pair(uow(engine), KEY, code, "X", attacker, NOW + timedelta(minutes=1)))

    assert blocked.code is ErrorCode.RATE_LIMITED
    assert blocked.details["retry_after_seconds"] == 14 * 60
    pair(uow(engine), KEY, code, "Другой адрес", ip(), NOW + timedelta(minutes=1))


# --- ротация -------------------------------------------------------------------


def test_refresh_returns_derived_pair(engine: Engine, user_id: int) -> None:
    _, first = paired(engine, user_id)
    later = NOW + timedelta(minutes=20)

    second = refresh(uow(engine), KEY, first.refresh_token, later)

    assert second.refresh_token == derive_token(KEY, Purpose.REFRESH, first.refresh_token)
    assert second.access_token == derive_token(KEY, Purpose.ACCESS, first.refresh_token)
    assert (second.access_expires_at, second.refresh_expires_at) == (later + ACCESS_TOKEN_LIFETIME, later + REFRESH_TOKEN_LIFETIME)
    assert authenticate_access(uow(engine), KEY, second.access_token, later).user.id == user_id


def test_replay_within_window_returns_same_pair(engine: Engine, user_id: int) -> None:
    _, first = paired(engine, user_id)
    second = refresh(uow(engine), KEY, first.refresh_token, NOW)

    replayed = refresh(uow(engine), KEY, first.refresh_token, NOW + REFRESH_REPLAY_WINDOW)

    assert replayed == second


def test_replay_after_window_revokes_whole_family(engine: Engine, user_id: int, caplog: pytest.LogCaptureFixture) -> None:
    _, first = paired(engine, user_id)
    second = refresh(uow(engine), KEY, first.refresh_token, NOW)
    third = refresh(uow(engine), KEY, second.refresh_token, NOW + timedelta(minutes=5))
    attack = NOW + REFRESH_REPLAY_WINDOW + timedelta(seconds=1)

    with caplog.at_level(logging.WARNING, logger="pricewatch.application.app_auth"):
        assert error_of(lambda: refresh(uow(engine), KEY, first.refresh_token, attack)).code is ErrorCode.REFRESH_TOKEN_REUSED

    # отозвано всё семейство, в том числе самые свежие токены
    later = NOW + timedelta(minutes=6)
    assert error_of(lambda: refresh(uow(engine), KEY, third.refresh_token, later)).code is ErrorCode.REFRESH_TOKEN_INVALID
    assert error_of(lambda: authenticate_access(uow(engine), KEY, third.access_token, later)).code is ErrorCode.AUTH_REQUIRED
    (record,) = [r for r in caplog.records if "Повтор refresh-токена" in r.getMessage()]
    assert record.user_id == user_id  # type: ignore[attr-defined]
    assert first.refresh_token not in str(vars(record))


def test_other_devices_survive_family_revocation(engine: Engine, user_id: int) -> None:
    _, stolen = paired(engine, user_id)
    _, other = paired(engine, user_id)
    refresh(uow(engine), KEY, stolen.refresh_token, NOW)
    error_of(lambda: refresh(uow(engine), KEY, stolen.refresh_token, NOW + timedelta(minutes=2)))

    assert authenticate_access(uow(engine), KEY, other.access_token, NOW).user.id == user_id


@pytest.mark.parametrize("token", ["", "выдуманный", "x" * 43])
def test_unknown_refresh_token(engine: Engine, token: str) -> None:
    assert error_of(lambda: refresh(uow(engine), KEY, token, NOW)).code is ErrorCode.REFRESH_TOKEN_INVALID


def test_expired_refresh_token(engine: Engine, user_id: int) -> None:
    _, tokens = paired(engine, user_id)

    assert error_of(
        lambda: refresh(uow(engine), KEY, tokens.refresh_token, NOW + REFRESH_TOKEN_LIFETIME)
    ).code is ErrorCode.REFRESH_TOKEN_INVALID


def test_refresh_for_disabled_user(engine: Engine, user_id: int) -> None:
    _, tokens = paired(engine, user_id)
    disable(engine, user_id)

    assert error_of(lambda: refresh(uow(engine), KEY, tokens.refresh_token, NOW)).code is ErrorCode.REFRESH_TOKEN_INVALID


# --- access-токен и выход ------------------------------------------------------


def test_access_token_expiry_boundary(engine: Engine, user_id: int) -> None:
    _, tokens = paired(engine, user_id)

    authenticate_access(uow(engine), KEY, tokens.access_token, NOW + ACCESS_TOKEN_LIFETIME - timedelta(seconds=1))
    assert error_of(
        lambda: authenticate_access(uow(engine), KEY, tokens.access_token, NOW + ACCESS_TOKEN_LIFETIME)
    ).code is ErrorCode.AUTH_REQUIRED


@pytest.mark.parametrize("token", [None, "", "выдуманный"])
def test_missing_or_unknown_access_token(engine: Engine, token: str | None) -> None:
    assert error_of(lambda: authenticate_access(uow(engine), KEY, token, NOW)).code is ErrorCode.AUTH_REQUIRED


def test_access_token_under_other_key(engine: Engine, user_id: int) -> None:
    _, tokens = paired(engine, user_id)

    assert error_of(lambda: authenticate_access(uow(engine), b"o" * 40, tokens.access_token, NOW)).code is ErrorCode.AUTH_REQUIRED


def test_disabled_user_access_stops(engine: Engine, user_id: int) -> None:
    _, tokens = paired(engine, user_id)
    disable(engine, user_id)

    assert error_of(lambda: authenticate_access(uow(engine), KEY, tokens.access_token, NOW)).code is ErrorCode.AUTH_REQUIRED


def test_logout_revokes_all_device_tokens_at_once(engine: Engine, user_id: int) -> None:
    device, first = paired(engine, user_id)
    second = refresh(uow(engine), KEY, first.refresh_token, NOW)

    logout_device(uow(engine), device.id, NOW)

    for token in (first.access_token, second.access_token):
        assert error_of(lambda: authenticate_access(uow(engine), KEY, token, NOW)).code is ErrorCode.AUTH_REQUIRED
    assert error_of(lambda: refresh(uow(engine), KEY, second.refresh_token, NOW)).code is ErrorCode.REFRESH_TOKEN_INVALID


def test_only_hashes_are_stored(engine: Engine, user_id: int) -> None:
    device, tokens = paired(engine, user_id)

    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT token_hash FROM access_tokens WHERE device_id = :d "
                "UNION ALL SELECT token_hash FROM refresh_tokens WHERE device_id = :d"
            ),
            {"d": device.id},
        ).scalars().all()
    assert sorted(stored) == sorted(
        [keyed_hash(KEY, Purpose.ACCESS, tokens.access_token), keyed_hash(KEY, Purpose.REFRESH, tokens.refresh_token)]
    )
    assert tokens.access_token not in stored and tokens.refresh_token not in stored


@pytest.mark.parametrize(
    ("protocol", "implementation"),
    [("PairingCodeRepository", "SqlPairingCodeRepository"), ("DeviceRepository", "SqlDeviceRepository"), ("TokenRepository", "SqlTokenRepository")],
)
def test_repositories_match_protocols(protocol: str, implementation: str) -> None:
    import inspect

    from pricewatch.application import ports
    from pricewatch.persistence import app_auth_repositories

    proto, impl = getattr(ports, protocol), getattr(app_auth_repositories, implementation)
    for name in [n for n in vars(proto) if not n.startswith("_")]:
        expected = list(inspect.signature(getattr(proto, name)).parameters)
        assert list(inspect.signature(getattr(impl, name)).parameters) == expected, name
