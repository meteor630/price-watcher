"""domain.backups: имя копии и срок хранения 30 суток."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from pricewatch.domain.backups import KEEP, backup_name, created_at, expired

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
