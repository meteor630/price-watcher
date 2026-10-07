"""Настройки проверки на настоящей базе: значения по умолчанию, сохранение по версиям, ошибки по полям, права, срок актуальности в итогах."""

from collections.abc import Iterator
from datetime import UTC, datetime, time

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, World

from pricewatch.application.check_settings import CheckForm, check_settings, save_check_settings
from pricewatch.application.item import item_detail
from pricewatch.application.runs import schedule_times
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
        db.execute(text("TRUNCATE users, stores, workers, settings CASCADE"))


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


KEYS = ("check_times", "stale_after_days", "title_similarity_min_percent", "price_jump_max_percent")


def save(engine: Engine, world: World, times: list[str], days: str = "7", versions: tuple[int, int] | None = None,  # type: ignore[no-untyped-def]
         title: str = "85", jump: str = "30"):
    """`versions` — (версия расписания, версия остальных настроек); None — текущие."""
    if versions is None:
        stored = check_settings(uow(engine), world.user, NOW).versions
    else:
        stored = {key: versions[0] if key == "check_times" else versions[1] for key in KEYS}
    form = CheckForm(times=times, numbers={"stale_after_days": days, "title_similarity_percent": title, "price_jump_percent": jump}, versions=stored)
    return save_check_settings(uow(engine), world.user, form, NOW)


def error(action) -> tuple[ErrorCode, object]:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code, caught.value.details


def test_defaults_until_admin_saves(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)

    current = check_settings(uow(engine), admin.user, NOW)

    assert (current.times, current.stale_after_days, current.title_similarity_percent, current.price_jump_percent) == ((time(9, 0),), 7, 85, 30)
    assert current.versions == dict.fromkeys(KEYS, 0)
    assert current.next_run_at == datetime(2026, 10, 7, 6, 0, tzinfo=UTC)  # NOW — 15:00 по Москве, следующая — завтра в 09:00


def test_save_sorts_skips_empty_and_reaches_scheduler(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)

    saved = save(engine, admin, ["21:30", "", "08:00"], days="3")
    again = save(engine, admin, ["21:30", "08:00", "13:15"], days="30")

    assert (saved.times, saved.versions["check_times"], saved.stale_after_days, saved.versions["stale_after_days"]) == ((time(8, 0), time(21, 30)), 1, 3, 1)
    assert saved.next_run_at == datetime(2026, 10, 6, 18, 30, tzinfo=UTC)
    assert (again.versions["check_times"], again.versions["stale_after_days"], again.stale_after_days) == (2, 2, 30)
    assert schedule_times(uow(engine)) == (time(8, 0), time(13, 15), time(21, 30))


def test_field_errors_are_collected_and_nothing_is_written(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)

    assert error(lambda: save(engine, admin, ["", "25:00", "10:00"], days="0")) == (
        ErrorCode.VALIDATION_ERROR, {"fields": {"check_time_1": "required", "check_time_2": "invalid", "stale_after_days": "out_of_range"}}
    )
    assert error(lambda: save(engine, admin, ["10:00", "10:00", "９:００"], days="31")) == (
        ErrorCode.VALIDATION_ERROR, {"fields": {"check_time_2": "duplicate", "check_time_3": "invalid", "stale_after_days": "out_of_range"}}
    )
    for days in ("abc", "-1", "٣", "", "7.5"):
        assert error(lambda: save(engine, admin, ["10:00", "", ""], days=days))[1] == {"fields": {"stale_after_days": "out_of_range"}}
    assert check_settings(uow(engine), admin.user, NOW).versions == dict.fromkeys(KEYS, 0)


def test_someone_elses_save_is_a_conflict(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)
    save(engine, admin, ["10:00", "", ""])

    assert error(lambda: save(engine, admin, ["11:00", "", ""], versions=(0, 0)))[0] is ErrorCode.VERSION_CONFLICT
    assert error(lambda: save(engine, admin, ["11:00", "", ""], versions=(1, 0)))[0] is ErrorCode.VERSION_CONFLICT
    assert schedule_times(uow(engine)) == (time(10, 0),)  # частичной записи нет


def test_only_active_admin(engine: Engine) -> None:
    user, blocked = World(engine), World(engine, Role.ADMIN)
    blocked.one("UPDATE users SET disabled_at = now() WHERE id = :u RETURNING id", u=blocked.user.id)

    for world in (user, blocked):
        assert error(lambda world=world: check_settings(uow(engine), world.user, NOW))[0] is ErrorCode.FORBIDDEN
        assert error(lambda world=world: save(engine, world, ["10:00", "", ""], versions=(0, 0)))[0] is ErrorCode.FORBIDDEN


def test_stale_after_days_changes_totals_and_item_card(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)
    lamp = admin.item(admin.category("Дом"), "Лампа")
    admin.seen(admin.link(lamp, admin.store()), NOW - 4 * DAY, 1000_00)
    assert admin.board().total_minor == 1000_00

    save(engine, admin, ["09:00", "", ""], days="4")  # ровно 4 суток — уже не актуальна

    card = item_detail(uow(engine), admin.user, lamp, NOW)
    assert admin.board().total_minor == 0
    assert (card.price_status.value, card.stale_after_days) == ("stale", 4)
    save(engine, admin, ["09:00", "", ""], days="5")
    assert admin.board().total_minor == 1000_00


def test_change_is_written_to_audit(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)

    save(engine, admin, ["10:00", "", ""], days="5")

    with engine.connect() as db:
        rows = db.execute(text("SELECT actor_id, action, before_json, after_json FROM audit_events")).all()
    assert [tuple(row) for row in rows] == [
        (admin.user.id, "check_settings_changed",
         {"check_times": ["09:00"], "stale_after_days": 7, "title_similarity_min_percent": 85, "price_jump_max_percent": 30},
         {"check_times": ["10:00"], "stale_after_days": 5, "title_similarity_min_percent": 85, "price_jump_max_percent": 30})
    ]


def test_thresholds_are_validated_and_saved(engine: Engine) -> None:
    admin = World(engine, Role.ADMIN)

    assert error(lambda: save(engine, admin, ["10:00", "", ""], title="49", jump="201")) == (
        ErrorCode.VALIDATION_ERROR, {"fields": {"title_similarity_percent": "out_of_range", "price_jump_percent": "out_of_range"}}
    )
    saved = save(engine, admin, ["10:00", "", ""], title="99", jump="5")

    assert (saved.title_similarity_percent, saved.price_jump_percent) == (99, 5)


def test_new_threshold_applies_to_undecided_for_users(engine: Engine) -> None:
    # Скачок +40 %: при пороге 30 % ждёт решения, при 50 % — засчитан сам; принятые и отклонённые не трогаются.
    admin = World(engine, Role.ADMIN)
    store = admin.store()
    link = admin.link(admin.item(admin.category("Дом"), "Лампа"), store)
    admin.seen(link, NOW - 3 * DAY, 1000_00)
    admin.seen(link, NOW - DAY, 1400_00, confirmation="pending")
    assert admin.board().total_minor == 1000_00 and admin.board().reviews_pending == 1

    save(engine, admin, ["09:00", "", ""], jump="50")

    assert admin.board().total_minor == 1400_00 and admin.board().reviews_pending == 0
