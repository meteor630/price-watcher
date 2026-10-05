"""Вход на сайт: неотличимые отказы, ограничение попыток, перехеширование, сессия и выход."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.web_auth import authenticate_session, check_csrf, login, logout
from pricewatch.domain.accounts import FAILURE_WINDOW, SESSION_LIFETIME
from pricewatch.domain.errors import SAFE_MESSAGES, ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, keyed_hash
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"web-login-test-key-" + b"k" * 16
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PASSWORD = "правильный пароль 42"


class CountingHasher(PasswordHasher):
    """Дешёвые параметры для скорости и счётчик фиктивных проверок."""

    def __init__(self) -> None:
        super().__init__(time_cost=1, memory_cost=8 * 1024)
        self.dummy_checks = 0

    def verify_dummy(self, password: str) -> None:
        self.dummy_checks += 1
        super().verify_dummy(password)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def hasher() -> CountingHasher:
    return CountingHasher()


def add_user(engine: Engine, hasher: PasswordHasher, *, disabled: bool = False, password_hash: str | None = None) -> str:
    name = f"w{uuid.uuid4().hex[:10]}"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (login, password_hash, role, display_name, disabled_at) "
                "VALUES (:l, :h, 'user', 'U', CASE WHEN :d THEN now() END)"
            ),
            {"l": name, "h": password_hash or hasher.hash(PASSWORD), "d": disabled},
        )
    return name


def sign_in(engine: Engine, hasher: PasswordHasher, name: str, password: str = PASSWORD, *, ip: str | None = "203.0.113.7", now: datetime = NOW):  # type: ignore[no-untyped-def]
    return login(SqlUnitOfWork(engine), hasher, KEY, name, password, ip, now)


def error_of(action) -> PriceWatcherError:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value


def unique_ip() -> str:
    return f"198.51.100.{uuid.uuid4().int % 250 + 1}"


# --- вход --------------------------------------------------------------------


def test_login_creates_session_storing_only_hash(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)

    issued = sign_in(engine, hasher, name.upper())  # логин без учёта регистра

    assert issued.user.login == name
    assert (issued.session.created_at, issued.session.expires_at) == (NOW, NOW + SESSION_LIFETIME)
    assert issued.csrf_token == keyed_hash(KEY, Purpose.CSRF, issued.token)
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT token_hash FROM web_sessions WHERE id = :id"), {"id": issued.session.id}
        ).scalar_one()
    assert stored == keyed_hash(KEY, Purpose.SESSION, issued.token)
    assert stored != issued.token


def test_wrong_password_unknown_login_and_disabled_user_are_indistinguishable(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    disabled = add_user(engine, hasher, disabled=True)

    errors = [
        error_of(lambda: sign_in(engine, hasher, name, "неверный пароль", ip=unique_ip())),
        error_of(lambda: sign_in(engine, hasher, f"nobody{uuid.uuid4().hex[:6]}", PASSWORD, ip=unique_ip())),
        error_of(lambda: sign_in(engine, hasher, disabled, PASSWORD, ip=unique_ip())),
        error_of(lambda: sign_in(engine, hasher, "не логин!", PASSWORD, ip=unique_ip())),
    ]

    assert {(e.code, e.safe_message, len(e.details)) for e in errors} == {
        (ErrorCode.INVALID_CREDENTIALS, SAFE_MESSAGES[ErrorCode.INVALID_CREDENTIALS], 0)
    }
    # для несуществующего логина и логина неверного формата argon2 всё равно выполнялся
    assert hasher.dummy_checks == 2


def test_failures_are_recorded_despite_error(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    ip = unique_ip()

    error_of(lambda: sign_in(engine, hasher, name, "неверный пароль", ip=ip))

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT kind, subject, host(ip) FROM auth_failures WHERE subject = :s"), {"s": name.lower()}
        ).all()
    assert [tuple(r) for r in rows] == [("login", name.lower(), ip)]


# --- ограничение попыток -----------------------------------------------------


def test_sixth_attempt_on_login_is_blocked_even_with_right_password(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    for minute in range(5):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=unique_ip(), now=NOW + timedelta(minutes=minute)))

    blocked = error_of(lambda: sign_in(engine, hasher, name, PASSWORD, ip=unique_ip(), now=NOW + timedelta(minutes=5)))

    assert blocked.code is ErrorCode.RATE_LIMITED
    # окно освободится, когда истечёт самая ранняя неудача: 12:00 + 15 мин − 12:05 = 10 мин
    assert blocked.details["retry_after_seconds"] == 600


def test_fifth_failure_still_allowed_to_try(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    for _ in range(4):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=unique_ip()))

    assert sign_in(engine, hasher, name, PASSWORD, ip=unique_ip()).user.login == name


def test_window_expiry_unblocks(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    for _ in range(5):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=unique_ip()))

    assert error_of(lambda: sign_in(engine, hasher, name, PASSWORD, now=NOW + FAILURE_WINDOW - timedelta(seconds=1))).code is (
        ErrorCode.RATE_LIMITED
    )
    assert sign_in(engine, hasher, name, PASSWORD, ip=unique_ip(), now=NOW + FAILURE_WINDOW).user.login == name


def test_blocked_attempts_do_not_extend_block(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    for _ in range(5):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=unique_ip()))
    for minute in range(1, 10):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=unique_ip(), now=NOW + timedelta(minutes=minute)))

    assert sign_in(engine, hasher, name, PASSWORD, ip=unique_ip(), now=NOW + FAILURE_WINDOW).user.login == name


def test_ip_limit_across_different_logins(engine: Engine, hasher: CountingHasher) -> None:
    ip = unique_ip()
    victim = add_user(engine, hasher)
    for _ in range(20):
        error_of(lambda: sign_in(engine, hasher, f"guess{uuid.uuid4().hex[:6]}", "неверный", ip=ip))

    assert error_of(lambda: sign_in(engine, hasher, victim, PASSWORD, ip=ip)).code is ErrorCode.RATE_LIMITED
    assert sign_in(engine, hasher, victim, PASSWORD, ip=unique_ip()).user.login == victim


def test_success_clears_login_failures(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    for _ in range(4):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=unique_ip()))
    sign_in(engine, hasher, name, ip=unique_ip())

    for _ in range(4):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=unique_ip()))
    assert sign_in(engine, hasher, name, ip=unique_ip()).user.login == name


def test_login_without_ip_counts_only_by_login(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    for _ in range(5):
        error_of(lambda: sign_in(engine, hasher, name, "неверный", ip=None))

    assert error_of(lambda: sign_in(engine, hasher, name, PASSWORD, ip=None)).code is ErrorCode.RATE_LIMITED


# --- перехеширование ---------------------------------------------------------


def test_weak_hash_is_upgraded_on_login(engine: Engine) -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024)
    strong = PasswordHasher(time_cost=2, memory_cost=16 * 1024)
    name = add_user(engine, weak, password_hash=weak.hash(PASSWORD))

    sign_in(engine, strong, name)

    with engine.connect() as connection:
        upgraded = connection.execute(text("SELECT password_hash FROM users WHERE login = :l"), {"l": name}).scalar_one()
    assert "m=16384,t=2" in upgraded
    assert strong.verify(upgraded, PASSWORD)


# --- сессия ------------------------------------------------------------------


def test_session_authenticates_and_touches_hourly(engine: Engine, hasher: CountingHasher) -> None:
    issued = sign_in(engine, hasher, add_user(engine, hasher))

    soon = authenticate_session(SqlUnitOfWork(engine), KEY, issued.token, NOW + timedelta(minutes=30))
    later = authenticate_session(SqlUnitOfWork(engine), KEY, issued.token, NOW + timedelta(hours=2))
    after = authenticate_session(SqlUnitOfWork(engine), KEY, issued.token, NOW + timedelta(hours=2, minutes=1))

    assert soon.user.id == issued.user.id
    assert soon.session.last_seen_at == NOW
    assert later.session.last_seen_at == NOW  # возвращён до отметки
    assert after.session.last_seen_at == NOW + timedelta(hours=2)
    assert after.csrf_token == issued.csrf_token


@pytest.mark.parametrize("token", [None, "", "выдуманный токен"])
def test_missing_or_unknown_session(engine: Engine, token: str | None) -> None:
    assert error_of(lambda: authenticate_session(SqlUnitOfWork(engine), KEY, token, NOW)).code is ErrorCode.AUTH_REQUIRED


def test_session_expiry_boundary(engine: Engine, hasher: CountingHasher) -> None:
    issued = sign_in(engine, hasher, add_user(engine, hasher))

    authenticate_session(SqlUnitOfWork(engine), KEY, issued.token, NOW + SESSION_LIFETIME - timedelta(seconds=1))
    assert error_of(
        lambda: authenticate_session(SqlUnitOfWork(engine), KEY, issued.token, NOW + SESSION_LIFETIME)
    ).code is ErrorCode.AUTH_REQUIRED


def test_session_of_disabled_user_stops_working(engine: Engine, hasher: CountingHasher) -> None:
    name = add_user(engine, hasher)
    issued = sign_in(engine, hasher, name)
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET disabled_at = now() WHERE login = :l"), {"l": name})

    assert error_of(lambda: authenticate_session(SqlUnitOfWork(engine), KEY, issued.token, NOW)).code is ErrorCode.AUTH_REQUIRED


def test_session_under_other_key_is_unknown(engine: Engine, hasher: CountingHasher) -> None:
    issued = sign_in(engine, hasher, add_user(engine, hasher))

    assert error_of(
        lambda: authenticate_session(SqlUnitOfWork(engine), b"x" * 40, issued.token, NOW)
    ).code is ErrorCode.AUTH_REQUIRED


def test_logout_revokes_and_is_idempotent(engine: Engine, hasher: CountingHasher) -> None:
    issued = sign_in(engine, hasher, add_user(engine, hasher))
    other = sign_in(engine, hasher, issued.user.login)

    logout(SqlUnitOfWork(engine), KEY, issued.token, NOW)
    logout(SqlUnitOfWork(engine), KEY, issued.token, NOW)
    logout(SqlUnitOfWork(engine), KEY, None, NOW)

    assert error_of(lambda: authenticate_session(SqlUnitOfWork(engine), KEY, issued.token, NOW)).code is ErrorCode.AUTH_REQUIRED
    assert authenticate_session(SqlUnitOfWork(engine), KEY, other.token, NOW).user.id == issued.user.id


# --- CSRF --------------------------------------------------------------------


def test_csrf_token_bound_to_session(engine: Engine, hasher: CountingHasher) -> None:
    first = sign_in(engine, hasher, add_user(engine, hasher))
    second = sign_in(engine, hasher, add_user(engine, hasher))

    check_csrf(KEY, first.token, first.csrf_token)
    for presented in (None, "", second.csrf_token, first.csrf_token[:-1] + "0", first.token):
        assert error_of(lambda: check_csrf(KEY, first.token, presented)).code is ErrorCode.CSRF_FAILED


@pytest.mark.parametrize("names", [("AuthFailureRepository", "SqlAuthFailureRepository"), ("SessionRepository", "SqlSessionRepository")])
def test_auth_repositories_match_protocols(names: tuple[str, str]) -> None:
    import inspect

    from pricewatch.application import ports
    from pricewatch.persistence import auth_repositories

    protocol, implementation = getattr(ports, names[0]), getattr(auth_repositories, names[1])
    for name in [n for n in vars(protocol) if not n.startswith("_")]:
        expected = list(inspect.signature(getattr(protocol, name)).parameters)
        assert list(inspect.signature(getattr(implementation, name)).parameters) == expected, name
