"""Вход на сайт: проверка пароля, ограничение попыток, серверная сессия, CSRF (раздел 4 архитектуры).

- Ограничения проверяются до пароля: при блокировке не проходит и верный пароль,
  иначе перебор узнал бы ответ, дождавшись его.
- Несуществующий логин, неверный пароль и отключённый пользователь неотличимы:
  один код INVALID_CREDENTIALS и та же цена проверки argon2.
- Неудача фиксируется до ответа и не откатывается; заблокированные попытки не пишутся,
  чтобы блокировка не продлевалась бесконечно.
- В базе только HMAC токена сессии; CSRF-токен — HMAC того же токена и нигде не хранится.
"""

from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.ports import FailureWindow, UnitOfWork
from pricewatch.domain.accounts import (
    FAILURE_WINDOW,
    LOGIN_FAILURES_PER_IP,
    LOGIN_FAILURES_PER_LOGIN,
    LOGIN_PATTERN,
    SESSION_LIFETIME,
    SESSION_TOUCH_INTERVAL,
    User,
    WebSession,
    login_key,
    retry_after_seconds,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.tokens import Purpose, keyed_hash, new_token, same_secret

LOGIN_FAILURE = "login"


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Новая сессия; token уходит в cookie и больше нигде не появляется."""

    user: User
    session: WebSession
    token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class SessionUser:
    user: User
    session: WebSession
    csrf_token: str


def csrf_token_for(key: bytes, session_token: str) -> str:
    return keyed_hash(key, Purpose.CSRF, session_token)


def check_csrf(key: bytes, session_token: str, presented: str | None) -> None:
    if not presented or not same_secret(csrf_token_for(key, session_token), presented):
        raise PriceWatcherError(ErrorCode.CSRF_FAILED)


def login(
    uow: UnitOfWork,
    hasher: PasswordHasher,
    key: bytes,
    login: str,
    password: str,
    ip: str | None,
    now: datetime,
) -> IssuedSession:
    subject = _subject(login)
    with uow:
        _ensure_not_limited(uow, subject, ip, now)
        credentials = uow.users.find_credentials(subject) if subject else None
        if credentials is None:
            hasher.verify_dummy(password)
            verified = False
        else:
            verified = hasher.verify(credentials.password_hash, password) and not credentials.user.disabled
        if credentials is None or not verified:
            uow.auth_failures.record(LOGIN_FAILURE, subject, ip, now)
            uow.commit()
            raise PriceWatcherError(ErrorCode.INVALID_CREDENTIALS)

        user = credentials.user
        uow.auth_failures.clear_subject(LOGIN_FAILURE, login_key(user.login))
        if hasher.needs_rehash(credentials.password_hash):
            uow.users.set_password_hash(user.id, hasher.hash(password))
        token = new_token()
        session = uow.sessions.create(user.id, keyed_hash(key, Purpose.SESSION, token), now, now + SESSION_LIFETIME)
        uow.commit()
    return IssuedSession(user=user, session=session, token=token, csrf_token=csrf_token_for(key, token))


def authenticate_session(uow: UnitOfWork, key: bytes, token: str | None, now: datetime) -> SessionUser:
    """Пользователь сессии или AUTH_REQUIRED: нет cookie, отозвана, истекла, пользователь отключён."""
    if not token:
        raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
    with uow:
        session = uow.sessions.find_active(keyed_hash(key, Purpose.SESSION, token), now)
        if session is None:
            raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
        user = uow.users.get(session.user_id)
        if user.disabled:
            raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
        if now - session.last_seen_at >= SESSION_TOUCH_INTERVAL:
            uow.sessions.touch(session.id, now)
            uow.commit()
    return SessionUser(user=user, session=session, csrf_token=csrf_token_for(key, token))


def logout(uow: UnitOfWork, key: bytes, token: str | None, now: datetime) -> None:
    """Отзывает сессию; повторный выход и выход без сессии ничего не меняют."""
    if not token:
        return
    with uow:
        session = uow.sessions.find_active(keyed_hash(key, Purpose.SESSION, token), now)
        if session is not None:
            uow.sessions.revoke(session.id, now)
            uow.commit()


def _subject(login: str) -> str | None:
    """Ключ логина для счётчика неудач; логин неверного формата учитывается только по IP."""
    candidate = login.strip()
    return login_key(candidate) if LOGIN_PATTERN.fullmatch(candidate) else None


def _ensure_not_limited(uow: UnitOfWork, subject: str | None, ip: str | None, now: datetime) -> None:
    since = now - FAILURE_WINDOW
    exceeded: list[FailureWindow] = []
    if subject is not None:
        window = uow.auth_failures.window_for_subject(LOGIN_FAILURE, subject, since)
        if window.count >= LOGIN_FAILURES_PER_LOGIN:
            exceeded.append(window)
    if ip is not None:
        window = uow.auth_failures.window_for_ip(LOGIN_FAILURE, ip, since)
        if window.count >= LOGIN_FAILURES_PER_IP:
            exceeded.append(window)
    if exceeded:
        retry_after = max(retry_after_seconds(window.oldest, now) for window in exceeded if window.oldest is not None)
        raise PriceWatcherError(ErrorCode.RATE_LIMITED, {"retry_after_seconds": retry_after})
