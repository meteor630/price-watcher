"""pricewatch.domain.shopping: записи списка покупок неизменяемы."""

import dataclasses
from datetime import UTC, datetime

import pytest

from pricewatch.domain.shopping import Category, Group, Item, ItemState


@pytest.mark.parametrize(
    "record",
    [
        Category(id=1, user_id=1, name="А", position=0, version=1),
        Group(id=1, user_id=1, category_id=1, name="Г", position=0, version=1),
        Item(
            id=1, user_id=1, category_id=1, group_id=None, name="Т", full_name=None,
            quantity=1, in_total=True, position=0, version=1, state=ItemState.ACTIVE,
            created_at=datetime(2026, 10, 7, tzinfo=UTC), purchase=None, archived_at=None,
        ),
    ],
)
def test_records_are_immutable(record: object) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.version = 2  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        record.extra = 1  # type: ignore[attr-defined]


def test_new_version_is_a_new_record() -> None:
    category = Category(id=1, user_id=1, name="А", position=0, version=1)

    renamed = dataclasses.replace(category, name="Б", version=2)

    assert (category.name, category.version) == ("А", 1)
    assert (renamed.name, renamed.version) == ("Б", 2)


def test_item_states_match_architecture() -> None:
    assert [state.value for state in ItemState] == ["active", "purchased", "archived"]
