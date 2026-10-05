"""Интерфейсы хранилища, которые реализует persistence, а связывает bootstrap.

Общие правила всех репозиториев:
- каждый метод принимает user_id и видит только записи этого пользователя;
  чужая, удалённая или несуществующая запись — NOT_FOUND, без различий;
- имена очищаются и сравниваются правилами pricewatch.domain.names;
  занятое имя — NAME_TAKEN;
- изменение и удаление принимают версию, которую видел клиент; устаревшая
  версия — VERSION_CONFLICT, в details["current"] — актуальная запись;
- перестановка принимает полный упорядоченный список и версию родителя:
  для категорий — версию порядка категорий пользователя, для групп и товаров
  без группы — версию категории, для товаров в группе — версию группы;
  неполный или чужой список — VALIDATION_ERROR, устаревшая версия — VERSION_CONFLICT;
- удаление мягкое; восстановление возвращает запись в конец списка.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from pricewatch.domain.accounts import Device, Invite, Role, User, WebSession
from pricewatch.domain.shopping import Category, Group, Item


class Unchanged:
    """Поле изменения не задано — значение остаётся прежним."""

    def __repr__(self) -> str:
        return "UNCHANGED"


UNCHANGED = Unchanged()


@dataclass(frozen=True, slots=True)
class NewItem:
    category_id: int
    name: str
    group_id: int | None = None
    full_name: str | None = None
    quantity: int = 1
    in_total: bool = True


@dataclass(frozen=True, slots=True)
class ItemChanges:
    name: str | Unchanged = UNCHANGED
    full_name: str | None | Unchanged = UNCHANGED
    quantity: int | Unchanged = UNCHANGED
    in_total: bool | Unchanged = UNCHANGED
    group_id: int | None | Unchanged = UNCHANGED


@dataclass(frozen=True, slots=True)
class UserCredentials:
    user: User
    password_hash: str


class UserRepository(Protocol):
    """Логин уникален без учёта регистра: занятый — LOGIN_TAKEN."""

    def create(self, login: str, password_hash: str, role: Role, display_name: str) -> User: ...
    def get(self, user_id: int) -> User: ...
    def find_credentials(self, login: str) -> UserCredentials | None:
        """Поиск по логину без учёта регистра, в том числе отключённого пользователя."""
        ...
    def set_password_hash(self, user_id: int, password_hash: str) -> None: ...


@dataclass(frozen=True, slots=True)
class FailureWindow:
    """Неудачи за окно: сколько и когда была самая ранняя (для времени ожидания)."""

    count: int
    oldest: datetime | None


class AuthFailureRepository(Protocol):
    def record(self, kind: str, subject: str | None, ip: str | None, now: datetime) -> None: ...
    def window_for_subject(self, kind: str, subject: str, since: datetime) -> FailureWindow: ...
    def window_for_ip(self, kind: str, ip: str, since: datetime) -> FailureWindow: ...
    def clear_subject(self, kind: str, subject: str) -> None: ...


class SessionRepository(Protocol):
    def create(self, user_id: int, token_hash: str, created_at: datetime, expires_at: datetime) -> WebSession: ...
    def find_active(self, token_hash: str, now: datetime) -> WebSession | None:
        """Неотозванная и неистёкшая сессия; отключённость пользователя проверяет сценарий."""
        ...
    def touch(self, session_id: int, now: datetime) -> None: ...
    def revoke(self, session_id: int, now: datetime) -> None: ...


class InviteRepository(Protocol):
    def create(self, code_hash: str, role: Role, created_by: int, created_at: datetime, expires_at: datetime) -> Invite:
        """created_at и expires_at — из одного источника времени, без часов базы."""
        ...
    def claim(self, code_hash: str, now: datetime) -> Invite | None:
        """Неиспользованное и неистёкшее приглашение, заблокированное до конца транзакции; иначе None."""
        ...
    def mark_used(self, invite_id: int, user_id: int, now: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class PairingClaim:
    id: int
    user_id: int
    expires_at: datetime
    used: bool


class PairingCodeRepository(Protocol):
    def create(self, user_id: int, code_hash: str, created_at: datetime, expires_at: datetime) -> None: ...
    def claim(self, code_hash: str) -> PairingClaim | None:
        """Код по HMAC, заблокированный до конца транзакции; истечение и использование проверяет сценарий."""
        ...
    def mark_used(self, code_id: int, now: datetime) -> None: ...


class DeviceRepository(Protocol):
    def create(self, user_id: int, name: str, created_at: datetime) -> Device: ...
    def get_active(self, device_id: int) -> Device | None: ...
    def touch(self, device_id: int, now: datetime) -> None: ...
    def revoke(self, device_id: int, now: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class RefreshRecord:
    id: int
    device_id: int
    used_at: datetime | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AccessGrant:
    device_id: int
    user_id: int


class TokenRepository(Protocol):
    def add_access(self, device_id: int, token_hash: str, issued_at: datetime, expires_at: datetime) -> None: ...
    def add_refresh(self, device_id: int, token_hash: str, issued_at: datetime, expires_at: datetime) -> None: ...
    def find_access(self, token_hash: str, now: datetime) -> AccessGrant | None:
        """Неистёкший access-токен неотозванного устройства."""
        ...
    def find_refresh(self, token_hash: str) -> RefreshRecord | None:
        """Refresh-токен по HMAC, заблокированный до конца транзакции."""
        ...
    def expires_of_access(self, token_hash: str) -> datetime | None: ...
    def expires_of_refresh(self, token_hash: str) -> datetime | None: ...
    def mark_refresh_used(self, refresh_id: int, now: datetime) -> None: ...


class CategoryRepository(Protocol):
    def list_active(self, user_id: int) -> list[Category]: ...
    def get(self, user_id: int, category_id: int) -> Category: ...
    def create(self, user_id: int, name: str) -> Category: ...
    def rename(self, user_id: int, category_id: int, name: str, version: int) -> Category: ...
    def delete(self, user_id: int, category_id: int, version: int) -> None: ...
    def restore(self, user_id: int, category_id: int) -> Category: ...
    def order_version(self, user_id: int) -> int: ...
    def reorder(self, user_id: int, ids: Sequence[int], order_version: int) -> int: ...


class GroupRepository(Protocol):
    def list_in_category(self, user_id: int, category_id: int) -> list[Group]: ...
    def get(self, user_id: int, group_id: int) -> Group: ...
    def create(self, user_id: int, category_id: int, name: str) -> Group: ...
    def rename(self, user_id: int, group_id: int, name: str, version: int) -> Group: ...
    def delete(self, user_id: int, group_id: int, version: int) -> None: ...
    def restore(self, user_id: int, group_id: int) -> Group: ...
    def reorder(self, user_id: int, category_id: int, ids: Sequence[int], category_version: int) -> int: ...


class ItemRepository(Protocol):
    def list_in_category(self, user_id: int, category_id: int) -> list[Item]: ...
    def get(self, user_id: int, item_id: int) -> Item: ...
    def create(self, user_id: int, new: NewItem) -> Item: ...
    def update(self, user_id: int, item_id: int, version: int, changes: ItemChanges) -> Item: ...
    def delete(self, user_id: int, item_id: int, version: int) -> None: ...
    def restore(self, user_id: int, item_id: int) -> Item: ...
    def reorder(
        self, user_id: int, category_id: int, group_id: int | None, ids: Sequence[int], parent_version: int
    ) -> int: ...


class UnitOfWork(Protocol):
    """Одна транзакция. Выход без commit() — откат."""

    users: UserRepository
    invites: InviteRepository
    auth_failures: AuthFailureRepository
    sessions: SessionRepository
    pairing_codes: PairingCodeRepository
    devices: DeviceRepository
    tokens: TokenRepository
    categories: CategoryRepository
    groups: GroupRepository
    items: ItemRepository

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def commit(self) -> None: ...
