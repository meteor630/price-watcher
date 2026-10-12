"""Telegram и токен сервиса на настоящей базе: код привязки одноразовый и с концом срока, чат у одного пользователя,
код для приложения только привязанному, сообщения для бота — захват без пересечения, повтор после сбоя, предел попыток."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from pricewatch.application.app_auth import pair
from pricewatch.application.services import authenticate_service, create_service, rotate_service_token
from pricewatch.application.telegram import (
    BATCH_MAX,
    CLAIM_FOR,
    LINK_CODE_LIFETIME,
    RETRY_AFTER,
    SEND_ATTEMPTS_MAX,
    claim_messages,
    is_linked,
    issue_link_code,
    link_chat,
    message_failed,
    message_sent,
    pairing_code_for_chat,
    unlink,
)
from pricewatch.domain.accounts import Role, User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.telegram_repository import SqlTelegramRepository
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"telegram-key-" + b"k" * 32
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
CHAT = 500_100_200


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, services, notifications, telegram_link_codes, audit_events CASCADE"))


def one(engine: Engine, sql: str, **params: object) -> object:
    with engine.begin() as db:
        return db.execute(text(sql), params).scalar_one()


def person(engine: Engine, role: Role = Role.USER, chat: int | None = None, disabled: bool = False) -> User:
    user_id = one(
        engine,
        "INSERT INTO users (login, password_hash, role, display_name, telegram_chat_id, disabled_at) "
        "VALUES ('t' || substr(md5(random()::text), 1, 10), 'h', :r, 'Аня', :c, CASE WHEN :d THEN now() END) RETURNING id",
        r=role.value, c=chat, d=disabled,
    )
    return User(user_id, "t", role, "Аня", disabled)  # type: ignore[arg-type]


def message(engine: Engine, user: User, kind: str = "unexpected_error", channel: str = "telegram", attempts: int = 0, repeats: int = 1,
            created: datetime = NOW - timedelta(minutes=5)) -> int:
    payload = '{"exception": "KeyError", "method": "GET", "route": "/web/", "error_id": "E-AAAAAAAAAA"}'
    return one(  # type: ignore[return-value]
        engine,
        "INSERT INTO notifications (user_id, channel, kind, dedup_key, payload_json, attempts, repeat_count, created_at) "
        "VALUES (:u, :ch, :k, md5(random()::text), CAST(:p AS jsonb), :a, :r, :t) RETURNING id",
        u=user.id, ch=channel, k=kind, p=payload, a=attempts, r=repeats, t=created,
    )


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


def refused(action) -> ErrorCode:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code


def chat_of(engine: Engine, user: User) -> object:
    return one(engine, "SELECT telegram_chat_id FROM users WHERE id = :u", u=user.id)


def test_link_code_links_chat_once_and_is_stored_only_as_hash(engine: Engine) -> None:
    user = person(engine)

    issued = issue_link_code(uow(engine), KEY, user, NOW)
    linked = link_chat(uow(engine), KEY, issued.code, CHAT, NOW + timedelta(minutes=1))

    assert linked.id == user.id and chat_of(engine, user) == CHAT and is_linked(uow(engine), user)
    assert issued.expires_at == NOW + LINK_CODE_LIFETIME
    assert one(engine, "SELECT count(*) FROM telegram_link_codes WHERE code_hash = :c", c=issued.code) == 0
    assert one(engine, "SELECT count(*) FROM audit_events WHERE actor_id = :u AND action = 'telegram_linked'", u=user.id) == 1
    assert refused(lambda: link_chat(uow(engine), KEY, issued.code, CHAT + 1, NOW + timedelta(minutes=2))) is ErrorCode.NOT_FOUND
    assert chat_of(engine, user) == CHAT


def test_expired_unknown_and_blocked_codes_link_nothing(engine: Engine) -> None:
    user, blocked = person(engine), person(engine, disabled=True)
    expiring = issue_link_code(uow(engine), KEY, user, NOW)
    of_blocked = issue_link_code(uow(engine), KEY, blocked, NOW)

    assert refused(lambda: link_chat(uow(engine), KEY, expiring.code, CHAT, NOW + LINK_CODE_LIFETIME)) is ErrorCode.NOT_FOUND
    assert refused(lambda: link_chat(uow(engine), KEY, "выдуманный-код", CHAT, NOW)) is ErrorCode.NOT_FOUND
    assert refused(lambda: link_chat(uow(engine), KEY, of_blocked.code, CHAT, NOW)) is ErrorCode.NOT_FOUND
    assert chat_of(engine, user) is None and chat_of(engine, blocked) is None


def test_chat_moves_to_the_last_user_who_linked_it(engine: Engine) -> None:
    first, second = person(engine, chat=CHAT), person(engine)

    link_chat(uow(engine), KEY, issue_link_code(uow(engine), KEY, second, NOW).code, CHAT, NOW)

    assert chat_of(engine, first) is None and chat_of(engine, second) == CHAT


def test_unlink_clears_chat_and_is_journaled(engine: Engine) -> None:
    user = person(engine, chat=CHAT)

    unlink(uow(engine), user, NOW)

    assert not is_linked(uow(engine), user)
    assert one(engine, "SELECT count(*) FROM audit_events WHERE actor_id = :u AND action = 'telegram_unlinked'", u=user.id) == 1


def test_pairing_code_only_for_linked_active_user(engine: Engine) -> None:
    user = person(engine, chat=CHAT)
    person(engine, chat=CHAT + 1, disabled=True)

    issued = pairing_code_for_chat(lambda: uow(engine), KEY, CHAT, NOW)
    device, _ = pair(uow(engine), KEY, issued.code, "iPhone", None, NOW + timedelta(minutes=1))

    assert device.user_id == user.id
    assert refused(lambda: pairing_code_for_chat(lambda: uow(engine), KEY, CHAT + 1, NOW)) is ErrorCode.NOT_FOUND
    assert refused(lambda: pairing_code_for_chat(lambda: uow(engine), KEY, CHAT + 2, NOW)) is ErrorCode.NOT_FOUND


def test_claim_takes_only_deliverable_telegram_messages(engine: Engine) -> None:
    linked, unlinked, blocked = person(engine, chat=CHAT), person(engine), person(engine, chat=CHAT + 1, disabled=True)
    wanted = message(engine, linked, repeats=3)
    message(engine, linked, channel="in_app")
    message(engine, linked, attempts=SEND_ATTEMPTS_MAX)
    message(engine, unlinked)
    message(engine, blocked)
    sent = message(engine, linked)
    message_sent(uow(engine), sent, NOW - timedelta(minutes=1))

    claimed = claim_messages(uow(engine), NOW, 10)

    assert [(m.notification_id, m.chat_id) for m in claimed] == [(wanted, CHAT)]
    assert claimed[0].text == "Неожиданная ошибка сервера: KeyError в GET /web/. Номер ошибки E-AAAAAAAAAA. Повторилось 3 раз."
    assert one(engine, "SELECT claimed_until FROM notifications WHERE id = :n", n=wanted) == NOW + CLAIM_FOR


def test_claimed_message_is_not_taken_again_until_claim_expires(engine: Engine) -> None:
    user = person(engine, chat=CHAT)
    note = message(engine, user)
    claim_messages(uow(engine), NOW, 10)

    assert claim_messages(uow(engine), NOW + CLAIM_FOR - timedelta(seconds=1), 10) == []
    assert [m.notification_id for m in claim_messages(uow(engine), NOW + CLAIM_FOR, 10)] == [note]


def test_two_bots_claiming_at_once_get_different_messages(engine: Engine) -> None:
    user = person(engine, chat=CHAT)
    notes = [message(engine, user, created=NOW - timedelta(minutes=10 - index)) for index in range(4)]

    with engine.connect() as first:
        first_batch = SqlTelegramRepository(first).claim_outbox(NOW, NOW + CLAIM_FOR, 2, SEND_ATTEMPTS_MAX)
        second_batch = claim_messages(uow(engine), NOW, 10)
        first.commit()

    assert [m.notification_id for m in first_batch] == notes[:2]
    assert [m.notification_id for m in second_batch] == notes[2:]


def test_failed_message_returns_after_pause_and_sent_one_never(engine: Engine) -> None:
    user = person(engine, chat=CHAT)
    note = message(engine, user)
    claim_messages(uow(engine), NOW, 10)

    message_failed(uow(engine), note, NOW)

    assert one(engine, "SELECT attempts FROM notifications WHERE id = :n", n=note) == 1
    assert claim_messages(uow(engine), NOW + RETRY_AFTER - timedelta(seconds=1), 10) == []
    assert [m.notification_id for m in claim_messages(uow(engine), NOW + RETRY_AFTER, 10)] == [note]
    message_sent(uow(engine), note, NOW + RETRY_AFTER)
    assert claim_messages(uow(engine), NOW + timedelta(hours=1), 10) == []
    assert refused(lambda: message_sent(uow(engine), note, NOW)) is ErrorCode.NOT_FOUND
    assert refused(lambda: message_failed(uow(engine), note, NOW)) is ErrorCode.NOT_FOUND


def test_in_app_and_unknown_messages_cannot_be_marked(engine: Engine) -> None:
    bell = message(engine, person(engine, chat=CHAT), channel="in_app")

    assert refused(lambda: message_sent(uow(engine), bell, NOW)) is ErrorCode.NOT_FOUND
    assert refused(lambda: message_failed(uow(engine), 2**62, NOW)) is ErrorCode.NOT_FOUND
    assert one(engine, "SELECT sent_at IS NULL AND attempts = 0 FROM notifications WHERE id = :n", n=bell) is True


def test_claim_batch_is_capped(engine: Engine) -> None:
    user = person(engine, chat=CHAT)
    for _ in range(BATCH_MAX + 1):
        message(engine, user)

    assert len(claim_messages(uow(engine), NOW, BATCH_MAX + 100)) == BATCH_MAX


def test_service_token_authenticates_touches_and_rotates(engine: Engine) -> None:
    token = create_service(uow(engine), KEY, "telegram-bot", NOW)

    service_id = authenticate_service(uow(engine), KEY, token, NOW + timedelta(minutes=1))
    rotated = rotate_service_token(uow(engine), KEY, "telegram-bot")
    still_old = authenticate_service(uow(engine), KEY, token, NOW + timedelta(minutes=2))
    authenticate_service(uow(engine), KEY, rotated, NOW + timedelta(minutes=3))

    assert service_id == still_old
    assert one(engine, "SELECT last_seen_at FROM services WHERE id = :s", s=service_id) == NOW + timedelta(minutes=3)
    assert one(engine, "SELECT count(*) FROM services WHERE token_hash = :t OR next_token_hash = :t", t=token) == 0
    assert refused(lambda: authenticate_service(uow(engine), KEY, token, NOW)) is ErrorCode.AUTH_REQUIRED


def test_service_refusals(engine: Engine) -> None:
    token = create_service(uow(engine), KEY, "telegram-bot", NOW)

    assert refused(lambda: create_service(uow(engine), KEY, "telegram-bot", NOW)) is ErrorCode.NAME_TAKEN
    assert refused(lambda: rotate_service_token(uow(engine), KEY, "нет-такого")) is ErrorCode.NOT_FOUND
    assert refused(lambda: authenticate_service(uow(engine), KEY, None, NOW)) is ErrorCode.AUTH_REQUIRED
    with engine.begin() as db:
        db.execute(text("UPDATE services SET disabled_at = now()"))
    assert refused(lambda: authenticate_service(uow(engine), KEY, token, NOW)) is ErrorCode.AUTH_REQUIRED
