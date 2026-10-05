"""Личный список покупок: категории, группы, товары.

Записи неизменяемы: изменение — это новая запись с новой версией из репозитория.
"""

from dataclasses import dataclass


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
