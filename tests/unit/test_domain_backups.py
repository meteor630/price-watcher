"""domain.backups: имя копии и срок хранения 30 суток."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from pricewatch.domain.backups import KEEP, STALE_AFTER, backup_name, created_at, expired, stale_since

NOW = datetime(2026, 10, 14, 1, 30, tzinfo=UTC)


def test_name_is_utc_moment_with_seconds() -> None:
    moscow = timezone(timedelta(hours=3))
    assert backup_name(datetime(2026, 10, 14, 4, 30, 5, tzinfo=moscow)) == "pricewatch-2026-10-14T013005Z.tar.age"


def test_name_round_trips_to_moment() -> None:
    assert created_at(backup_name(NOW)) == NOW


def test_naive_moment_is_rejected() -> None:
    with pytest.raises(ValueError):
        backup_name(datetime(2026, 10, 14, 1, 30))


@pytest.mark.parametrize(
    "name",
    ["notes.txt", "pricewatch-2026-10-14T013000Z.tar", ".pricewatch-2026-10-14T013000Z.tar.age.partial", "pricewatch-2026-13-40T999999Z.tar.age"],
)
def test_other_files_are_not_backups(name: str) -> None:
    assert created_at(name) is None


def test_only_backups_older_than_thirty_days_expire_oldest_first() -> None:
    names = [
        backup_name(NOW - KEEP - timedelta(days=2)),
        "notes.txt",
        backup_name(NOW - KEEP),
        backup_name(NOW - KEEP - timedelta(seconds=1)),
        backup_name(NOW - timedelta(days=1)),
    ]
    assert expired(names, NOW) == [backup_name(NOW - KEEP - timedelta(days=2)), backup_name(NOW - KEEP - timedelta(seconds=1))]


def test_nothing_expires_in_empty_folder() -> None:
    assert expired([], NOW) == []


# --- нет удачной копии больше 36 часов ---


def test_recent_successful_copy_is_not_stale() -> None:
    assert stale_since(NOW - STALE_AFTER + timedelta(minutes=1), NOW - timedelta(days=5), NOW) is None


def test_exactly_36_hours_is_not_yet_stale() -> None:
    assert stale_since(NOW - STALE_AFTER, None, NOW) is None


def test_older_than_36_hours_is_stale_since_last_success() -> None:
    last_ok = NOW - STALE_AFTER - timedelta(seconds=1)
    assert stale_since(last_ok, NOW - timedelta(days=9), NOW) == last_ok


def test_without_any_success_counts_from_first_attempt() -> None:
    first = NOW - timedelta(days=2)
    assert stale_since(None, first, NOW) == first
    assert stale_since(None, NOW - timedelta(hours=1), NOW) is None


def test_without_attempts_backups_are_not_set_up() -> None:
    assert stale_since(None, None, NOW) is None
