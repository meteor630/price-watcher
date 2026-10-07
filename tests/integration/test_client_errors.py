"""Отчёты об ошибках приложения на настоящей базе: очистка текста, уведомление со склейкой, лимит, время, хранение 90 суток."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import NOW, World

from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.client_errors import ClientReport, purge_client_errors, report_client_error
from pricewatch.application.notifications import bell_page
from pricewatch.domain.accounts import Role
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings, notifications CASCADE"))


def device(world: World) -> DeviceUser:
    return DeviceUser(world.user, world.one("INSERT INTO devices (user_id, name) VALUES (:u, 'iPhone') RETURNING id", u=world.user.id))


def report(message: str = "Ошибка декодирования", code: str = "decoding", at: object = NOW - timedelta(minutes=1)) -> ClientReport:
    return ClientReport("1.4.0", "iOS 26.0", "Товар", code, message, at)  # type: ignore[arg-type]


def send(engine: Engine, phone: DeviceUser, error_id: str, body: ClientReport, now: object = NOW) -> None:
    report_client_error(SqlUnitOfWork(engine), phone, error_id, body, now)  # type: ignore[arg-type]


def error(action) -> tuple[ErrorCode, object]:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code, caught.value.details


def test_report_is_cleaned_stored_and_notified_with_repeats(engine: Engine) -> None:
    admin, user = World(engine, Role.ADMIN), World(engine)
    phone = device(user)

    send(engine, phone, "E-AAAAAAAAAA", report("GET https://prices.example.ru/api/v1/items?token=s3cr3t упал, Bearer abc.def"))
    send(engine, phone, "E-BBBBBBBBBB", report("снова"))

    stored = user.one("SELECT message FROM client_error_reports WHERE error_id = 'E-AAAAAAAAAA'")
    assert stored == "GET https://prices.example.ru/api/v1/items упал, Bearer [скрыто]" and "s3cr3t" not in stored
    with engine.connect() as db:
        rows = db.execute(text("SELECT kind, repeat_count, payload_json->>'error_id' FROM notifications WHERE user_id = :u AND channel = 'in_app'"),
                          {"u": admin.user.id}).all()
    assert [tuple(row) for row in rows] == [("client_error", 2, "E-BBBBBBBBBB")]  # одна версия и код — склеиваются
    [item] = bell_page(SqlUnitOfWork(engine), admin.user, 0, 20).items
    assert item.text == "Ошибка приложения 1.4.0 (iOS 26.0) на экране «Товар»: decoding — снова. Номер ошибки E-BBBBBBBBBB."


def test_limit_per_device_per_hour(engine: Engine) -> None:
    user = World(engine)
    phone, tablet = device(user), device(user)
    for index in range(20):
        sent = NOW - timedelta(minutes=59) + timedelta(seconds=index)
        send(engine, phone, f"E-LIMIT{index:05d}", report(at=sent), sent)

    assert error(lambda: send(engine, phone, "E-OVERLIMIT", report())) == (ErrorCode.RATE_LIMITED, {"retry_after_seconds": 60})
    send(engine, tablet, "E-OTHERDEV1", report())  # лимит — на устройство
    send(engine, phone, "E-AFTERHOUR", report(), NOW + timedelta(minutes=1))


def test_time_is_checked(engine: Engine) -> None:
    phone = device(World(engine))

    assert error(lambda: send(engine, phone, "E-FUTURE0001", report(at=NOW + timedelta(minutes=11)))) == (
        ErrorCode.VALIDATION_ERROR, {"fields": {"occurred_at": "in_future"}}
    )
    send(engine, phone, "E-SKEW000001", report(at=NOW + timedelta(minutes=10)))  # часы устройства немного спешат
    assert error(lambda: send(engine, phone, "E-NAIVE00001", report(at=NOW.replace(tzinfo=None))))[1] == {"fields": {"occurred_at": "no_timezone"}}


def test_reports_kept_for_90_days(engine: Engine) -> None:
    phone = device(World(engine))
    old, new = NOW - timedelta(days=90, seconds=1), NOW - timedelta(days=89)
    send(engine, phone, "E-OLD0000001", report(at=old), old)
    send(engine, phone, "E-NEW0000001", report(at=new), new)

    assert purge_client_errors(SqlUnitOfWork(engine), NOW) == 1
    with engine.connect() as db:
        assert db.execute(text("SELECT error_id FROM client_error_reports")).scalars().all() == ["E-NEW0000001"]
