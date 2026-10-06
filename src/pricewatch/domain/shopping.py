"""Личный список покупок: категории, группы, товары.

Записи неизменяемы: изменение — это новая запись с новой версией из репозитория.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

# Мягко удалённое стирается окончательно через 30 дней (DEVELOPMENT_ARCHITECTURE.md, раздел 5).
PURGE_AFTER = timedelta(days=30)


class ItemState(StrEnum):
    """Жизненный цикл товара: в списке, куплен, в архиве (DEVELOPMENT_ARCHITECTURE.md, раздел 6)."""

    ACTIVE = "active"
    PURCHASED = "purchased"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class Category:
    id: int
    user_id: int
    name: str
    position: int
    version: int


@dataclass(frozen=True, slots=True)
class Group:
    id: int
    user_id: int
    category_id: int
    name: str
    position: int
    version: int


@dataclass(frozen=True, slots=True)
class Item:
    id: int
    user_id: int
    category_id: int
    group_id: int | None
    name: str
    full_name: str | None
    quantity: int
    in_total: bool
    position: int
    version: int
