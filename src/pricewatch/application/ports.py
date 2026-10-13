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
from pricewatch.domain.review import Checked, Decision
from pricewatch.domain.shopping import Category, Group, Item, ItemState, Purchase


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
    include_stale: bool | Unchanged = UNCHANGED
    group_id: int | None | Unchanged = UNCHANGED
    # Другая категория: группа берётся из этих же изменений (по умолчанию — без группы).
    category_id: int | Unchanged = UNCHANGED


@dataclass(frozen=True, slots=True)
class UserCredentials:
    user: User
    password_hash: str


@dataclass(frozen=True, slots=True)
class UserOverview:
    user_id: int
    login: str
    display_name: str
    role: Role
    created_at: datetime
    disabled_at: datetime | None
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class InviteOverview:
    invite_id: int
    role: Role
    created_by_name: str
    created_at: datetime
    expires_at: datetime


class UserRepository(Protocol):
    """Логин уникален без учёта регистра: занятый — LOGIN_TAKEN."""

    def create(self, login: str, password_hash: str, role: Role, display_name: str) -> User: ...
    def get(self, user_id: int) -> User: ...
    def find_credentials(self, login: str) -> UserCredentials | None:
        """Поиск по логину без учёта регистра, в том числе отключённого пользователя."""
        ...
    def set_password_hash(self, user_id: int, password_hash: str) -> None: ...
    def overview(self) -> list["UserOverview"]:
        """Все пользователи с ролью, датой регистрации, блокировки и последнего входа на сайт."""
        ...
    def lock_admins(self) -> list[int]:
        """Действующие администраторы; их строки блокируются до конца транзакции (два администратора
        не заблокируют друг друга одновременно, оставив систему без администратора)."""
        ...
    def set_disabled(self, user_id: int, disabled_at: datetime | None) -> None: ...
    def set_role(self, user_id: int, role: Role) -> None: ...


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
    def revoke_all(self, user_id: int, now: datetime) -> int: ...
    def revoke_others(self, user_id: int, keep_session_id: int, now: datetime) -> int:
        """Отозвать все сессии пользователя, кроме текущей."""
        ...


class InviteRepository(Protocol):
    def create(self, code_hash: str, role: Role, created_by: int, created_at: datetime, expires_at: datetime) -> Invite:
        """created_at и expires_at — из одного источника времени, без часов базы."""
        ...
    def claim(self, code_hash: str, now: datetime) -> Invite | None:
        """Неиспользованное и неистёкшее приглашение, заблокированное до конца транзакции; иначе None."""
        ...
    def mark_used(self, invite_id: int, user_id: int, now: datetime) -> None: ...
    def unused(self) -> list["InviteOverview"]:
        """Неиспользованные приглашения, сначала новые (и с истёкшим сроком — их можно удалить)."""
        ...
    def delete_unused(self, invite_ids: Sequence[int]) -> list[int]:
        """Удалить неиспользованные приглашения; номера удалённых."""
        ...


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


@dataclass(frozen=True, slots=True)
class DeviceOverview:
    device_id: int
    name: str
    created_at: datetime
    last_seen_at: datetime


class DeviceRepository(Protocol):
    def create(self, user_id: int, name: str, created_at: datetime) -> Device: ...
    def get_active(self, device_id: int) -> Device | None: ...
    def touch(self, device_id: int, now: datetime) -> None: ...
    def revoke(self, device_id: int, now: datetime) -> None: ...
    def revoke_all(self, user_id: int, now: datetime) -> int:
        """Отозвать все устройства пользователя — вместе с их токенами."""
        ...
    def list_active(self, user_id: int) -> list["DeviceOverview"]:
        """Неотозванные устройства пользователя, сначала недавно активные."""
        ...
    def revoke_owned(self, user_id: int, device_id: int, now: datetime) -> bool:
        """Отозвать своё устройство; чужое или уже отозванное — False."""
        ...


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
    def move_category_items(self, user_id: int, category_id: int, target_category_id: int) -> int:
        """Перенести все товары категории в другую категорию без группы, в конец списка; число перенесённых."""
        ...
    def ungroup_items(self, user_id: int, group_id: int) -> int:
        """Товары группы — в «без группы» той же категории, в конец списка; число перенесённых."""
        ...
    def purchase(self, user_id: int, item_id: int, version: int, purchase: Purchase, baseline_minor: int | None) -> Item:
        """Товар «в списке» — купленный; `baseline_minor` — лучшая цена в момент добавления товара."""
        ...
    def archive(self, user_id: int, item_id: int, version: int, now: datetime) -> Item: ...
    def return_to_list(self, user_id: int, item_id: int, version: int) -> Item:
        """Купленный или архивный товар — снова «в списке» на прежнем месте; данные покупки стираются."""
        ...


class PersonalDataRepository(Protocol):
    def blocked_owners(self, blocked_before: datetime) -> list[int]:
        """Пользователи, заблокированные не позже `blocked_before`, у которых остались неудалённые данные;
        строки пользователей блокируются, занятые другой транзакцией пропускаются до следующего раза."""
        ...
    def soft_delete_all(self, user_id: int, now: datetime) -> None:
        """Мягко удалить все товары, группы и категории пользователя (как обычное удаление: deleted_at, версия)."""
        ...
    def purge_deleted(self, deleted_before: datetime) -> "PurgedCounts":
        """Окончательно стереть удалённые не позже `deleted_before` категории, группы, товары вместе с содержимым
        и привязками ссылок; каталог ссылок и история цен остаются."""
        ...
    def restore_deleted_at(self, user_id: int, deleted_at: datetime) -> int:
        """Вернуть категории, группы и товары пользователя, удалённые ровно в этот момент (удаление после блокировки);
        уже стёртые очисткой не вернуть. Число возвращённых записей."""
        ...


@dataclass(frozen=True, slots=True)
class PurgedCounts:
    categories: int = 0
    groups: int = 0
    items: int = 0


# --- Повтор изменяющих запросов приложения (этап 7) ---


@dataclass(frozen=True, slots=True)
class StoredRequest:
    request_hash: str
    # Ответа ещё нет: запрос с этим ключом выполняется.
    status: int | None
    body: object | None


class IdempotencyRepository(Protocol):
    def drop_stale(self, user_id: int, key: str, expired_before: datetime, abandoned_before: datetime) -> None:
        """Удалить ключ, если срок ответа истёк или выполнение оборвалось без ответа."""
        ...
    def claim(self, user_id: int, key: str, request_hash: str, now: datetime) -> bool:
        """Занять ключ до выполнения; False — ключ уже есть (одновременная вставка ждёт первую)."""
        ...
    def get(self, user_id: int, key: str) -> StoredRequest | None: ...
    def complete(self, user_id: int, key: str, status: int, body: object) -> None: ...
    def release(self, user_id: int, key: str) -> None: ...
    def purge(self, created_before: datetime) -> int: ...


# --- Чтение цен для страниц (этап 7) ---


@dataclass(frozen=True, slots=True)
class PricedItem:
    """Товар для расчёта цен: сегодняшние количество, отметки и состояние."""

    item_id: int
    category_id: int
    group_id: int | None
    version: int
    name: str
    quantity: int
    in_total: bool
    include_stale: bool
    state: ItemState
    purchased_at: datetime | None
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReviewState:
    baseline_title: str | None
    decisions: dict[int, Decision]


@dataclass(frozen=True, slots=True)
class PurchasedItem:
    """Купленный товар для страниц: покупка и лучшая цена в момент добавления товара (если была)."""

    item_id: int
    category_id: int
    category_name: str
    name: str
    purchase: Purchase
    baseline_minor: int | None


@dataclass(frozen=True, slots=True)
class LinkBinding:
    item_id: int
    tracked_link_id: int
    store_id: int
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True, slots=True)
class StoreInfo:
    store_id: int
    name: str
    color_token: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class LinkInfo:
    url: str
    image_path: str | None


@dataclass(frozen=True, slots=True)
class LinkState:
    item_link_id: int
    url: str
    store_name: str
    color_token: str
    store_enabled: bool
    checking: bool
    last_status: str | None
    last_price_minor: int | None
    last_card_price_minor: int | None
    last_availability: str | None
    last_source: str | None
    last_checked_at: datetime | None


class PriceDataRepository(Protocol):
    def items(self, user_id: int) -> list[PricedItem]:
        """Неудалённые товары в неудалённых категориях и группах, в порядке категорий и позиций."""
        ...
    def purchases(self, user_id: int, category_id: int | None = None) -> list[PurchasedItem]:
        """Купленные товары, видимые на страницах (как в `items`), сначала новые; `category_id` — только этой категории."""
        ...
    def bindings(self, user_id: int) -> list[LinkBinding]:
        """Все привязки ссылок к этим товарам, в том числе закрытые: история цены не обрывается."""
        ...
    def stores(self, store_ids: Sequence[int]) -> dict[int, StoreInfo]: ...
    def links(self, tracked_link_ids: Sequence[int]) -> dict[int, LinkInfo]:
        """Адрес страницы в магазине и путь сохранённого фото ссылки."""
        ...
    def observations(self, tracked_link_ids: Sequence[int], since: datetime) -> dict[int, list[Checked]]:
        """Наблюдения ссылок (с номером и названием — для решений пользователя), нужные для расчёта на любой момент начиная с `since`: всё с `since`
        и раньше — начиная с последнего успешного подтверждённого до `since` (или последнего любого,
        если успешных не было). Более старая история на такие расчёты не влияет."""
        ...
    def review_states(self, user_id: int, tracked_link_ids: Sequence[int]) -> dict[int, "ReviewState"]:
        """Решения пользователя по наблюдениям этих ссылок и его эталон названия
        (название последнего принятого им наблюдения, иначе общий эталон ссылки)."""
        ...
    def link_states(self, user_id: int, item_id: int) -> list["LinkState"]:
        """Действующие ссылки товара: магазин, включён ли он, идёт ли проверка, последняя проверка."""
        ...


# --- Сбор цен и очередь (этап 6) ---


class LinkTarget(Protocol):
    """Что сборщик цен знает об адресе: приведённая ссылка и магазин (реализация — pricewatch.parsers.catalog)."""

    @property
    def normalized_url(self) -> str: ...
    @property
    def store_host(self) -> str: ...
    @property
    def parser_key(self) -> str: ...
    @property
    def store_name(self) -> str: ...
    @property
    def has_card_price(self) -> bool: ...
    @property
    def min_delay_ms(self) -> int: ...
    @property
    def auto_created(self) -> bool: ...


class LinkResolver(Protocol):
    def resolve(self, raw_url: str) -> LinkTarget:
        """Привести адрес и определить магазин; плохой адрес — INVALID_URL."""
        ...


@dataclass(frozen=True, slots=True)
class StoreRecord:
    id: int
    canonical_host: str
    display_name: str
    parser_key: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ItemLinkRecord:
    id: int
    item_id: int
    tracked_link_id: int
    store_id: int
    valid_from: datetime


@dataclass(frozen=True, slots=True)
class StorePause:
    store_id: int
    display_name: str
    canonical_host: str
    reason: str | None
    level: int


@dataclass(frozen=True, slots=True)
class StoreOverview:
    """Магазин для администратора: сколько ссылок каталога и сколько пользователей следят за ним сейчас."""

    store_id: int
    canonical_host: str
    display_name: str
    color_token: str
    has_card_price: bool
    enabled: bool
    auto_created: bool
    fetch_method: str | None
    paused_until: datetime | None
    pause_reason: str | None
    link_count: int
    watcher_count: int
    used: bool


class StoreRepository(Protocol):
    def get_or_create(self, target: LinkTarget, now: datetime) -> StoreRecord:
        """Магазин по имени сайта; нет — создаётся. Одновременное создание даёт одну строку."""
        ...
    def learn(self, store_id: int, fetch_method: str | None, site_name: str | None) -> None:
        """Запомнить сработавший способ; у созданного автоматически магазина — название с сайта вместо имени сайта."""
        ...
    def pause_state(self, store_id: int) -> StorePause:
        """Причина и ступень паузы магазина (строка блокируется до конца транзакции)."""
        ...
    def pause(self, store_id: int, reason: str, level: int, until: datetime) -> None: ...
    def clear_pause(self, store_id: int) -> None:
        """Магазин ответил не блокировкой и не капчей: пауза и ступень сбрасываются."""
        ...
    def overview(self) -> list[StoreOverview]:
        """Все магазины по названию; `used` — есть ссылки каталога, привязки или покупки в этом магазине."""
        ...
    def update(self, store_id: int, *, display_name: str, color: str) -> StoreOverview:
        """Новое название и цвет; нет магазина — NOT_FOUND."""
        ...
    def set_enabled(self, store_id: int, enabled: bool) -> StoreOverview: ...
    def delete_unused(self, store_id: int) -> StoreOverview:
        """Удалить магазин без ссылок, привязок и покупок; используемый — VALIDATION_ERROR (store: in_use)."""
        ...


class TrackedLinkRepository(Protocol):
    def get_or_create(self, store_id: int, normalized_url: str, region_code: str, now: datetime) -> int:
        """Номер ссылки каталога; одновременное добавление одной страницы даёт одну строку."""
        ...
    def baseline_title(self, tracked_link_id: int) -> str | None: ...
    def set_baseline_title(self, tracked_link_id: int, title: str) -> None: ...
    def set_image_if_missing(self, tracked_link_id: int, image_path: str) -> bool:
        """Записать путь фото, если у ссылки его ещё нет; False — фото уже есть."""
        ...


class ItemLinkRepository(Protocol):
    def lock_item(self, user_id: int, item_id: int) -> None:
        """Заблокировать товар владельца до конца транзакции; чужой или удалённый — NOT_FOUND."""
        ...
    def active_for_store(self, item_id: int, store_id: int) -> ItemLinkRecord | None: ...
    def bind(self, user_id: int, item_id: int, tracked_link_id: int, store_id: int, now: datetime) -> ItemLinkRecord: ...
    def close(self, item_link_id: int, now: datetime) -> None: ...
    def active_binding(self, user_id: int, item_id: int, item_link_id: int) -> ItemLinkRecord | None:
        """Действующая привязка этого товара владельца; иначе None."""
        ...
    def reopen(self, user_id: int, item_link_id: int) -> bool:
        """Вернуть закрытую привязку; False — у товара уже есть другая действующая ссылка этого магазина."""
        ...
    def clear_include_stale(self, tracked_link_id: int) -> None:
        """Цена ссылки снова актуальна: у товаров с этой действующей ссылкой снимается «устаревшая цена в итоге»."""
        ...
    def added_since(self, user_id: int, since: datetime) -> list[datetime]:
        """Время привязок пользователя позже `since`, по возрастанию — для лимита новых ссылок."""
        ...


@dataclass(frozen=True, slots=True)
class NewObservation:
    tracked_link_id: int
    task_id: int
    checked_at: datetime
    status: str
    price_minor: int | None
    card_price_minor: int | None
    availability: str
    title: str | None
    region_code: str
    confirmation: str
    source: str | None


@dataclass(frozen=True, slots=True)
class PendingObservation:
    id: int
    title: str | None
    effective_price_minor: int | None


class ObservationRepository(Protocol):
    def has_confirmed_success_since(self, tracked_link_id: int, since: datetime) -> bool: ...
    def exists_for_task(self, task_id: int) -> bool: ...
    def add(self, observation: NewObservation) -> None: ...
    def last_confirmed_price(self, tracked_link_id: int) -> int | None:
        """Действующая цена (по карте, если есть) последней подтверждённой успешной проверки в наличии."""
        ...
    def pending(self, tracked_link_id: int) -> list[PendingObservation]:
        """Наблюдения ссылки, ждущие решения."""
        ...
    def reject(self, observation_ids: Sequence[int]) -> None: ...
    def link_of(self, observation_id: int) -> int | None:
        """Ссылка наблюдения; None — такого наблюдения нет."""
        ...


@dataclass(frozen=True, slots=True)
class WorkerAuth:
    worker_id: int
    disabled: bool
    # Worker пришёл с новым токеном ротации — старый пора погасить.
    used_next_token: bool


@dataclass(frozen=True, slots=True)
class Silence:
    """Процесс (worker или сервис), замолчавший или снова вышедший на связь."""

    process_id: int
    name: str
    silent_since: datetime
    last_seen_at: datetime


class WorkerRepository(Protocol):
    def create(self, name: str, token_hash: str, now: datetime) -> int:
        """Новый worker; занятое имя — NAME_TAKEN."""
        ...
    def find_by_token(self, token_hash: str) -> WorkerAuth | None: ...
    def promote_next_token(self, worker_id: int) -> None: ...
    def set_next_token(self, name: str, token_hash: str) -> None:
        """Новый токен ротации; нет такого worker — NOT_FOUND."""
        ...
    def touch(self, worker_id: int, now: datetime) -> None: ...
    def report_silent(self, seen_before: datetime) -> list[Silence]:
        """Действующие процессы, последний пульс которых раньше `seen_before`, о молчании которых ещё не сообщено:
        начало молчания (последний пульс) запоминается. Процесс, ни разу не выходивший на связь, не молчит."""
        ...
    def report_recovered(self) -> list[Silence]:
        """Процессы, о молчании которых сообщено, и с тех пор был пульс: отметка молчания снимается."""
        ...
    def last_seen(self) -> datetime | None:
        """Когда действующий (не отключённый) worker последний раз обращался к серверу; None — ни разу."""
        ...


class ServiceRepository(Protocol):
    """Служебные процессы /internal/v1 (бот): тот же механизм токена, что у worker (WorkerAuth — владелец токена)."""

    def create(self, name: str, token_hash: str, now: datetime) -> int:
        """Новый сервис; занятое имя — NAME_TAKEN."""
        ...
    def find_by_token(self, token_hash: str) -> WorkerAuth | None: ...
    def promote_next_token(self, worker_id: int) -> None: ...
    def set_next_token(self, name: str, token_hash: str) -> None: ...
    def touch(self, worker_id: int, now: datetime) -> None: ...
    def report_silent(self, seen_before: datetime) -> list[Silence]:
        """Как у worker."""
        ...
    def report_recovered(self) -> list[Silence]:
        """Как у worker."""
        ...


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    """Telegram-сообщение, взятое ботом на отправку."""

    notification_id: int
    chat_id: int
    kind: str
    payload: dict[str, object]
    repeat_count: int


class TelegramRepository(Protocol):
    def create_link_code(self, user_id: int, code_hash: str, now: datetime, expires_at: datetime) -> None: ...
    def use_link_code(self, code_hash: str, now: datetime) -> int | None:
        """Погасить действующий код привязки; чей он. Неизвестный, использованный или истёкший — None."""
        ...
    def set_chat(self, user_id: int, chat_id: int) -> None:
        """Привязать чат к пользователю; этот чат у другого пользователя отвязывается (один Telegram — один пользователь)."""
        ...
    def clear_chat(self, user_id: int) -> None: ...
    def is_linked(self, user_id: int) -> bool: ...
    def user_by_chat(self, chat_id: int) -> User | None: ...
    def claim_outbox(self, now: datetime, claimed_until: datetime, limit: int, attempts_max: int) -> list[OutboxMessage]:
        """Взять неотправленные Telegram-сообщения тех, у кого есть чат: не взятые другим экземпляром бота
        (или его захват истёк) и не исчерпавшие попытки; захват — до `claimed_until`."""
        ...
    def mark_sent(self, notification_id: int, now: datetime) -> bool: ...
    def mark_failed(self, notification_id: int, retry_at: datetime) -> bool:
        """Попытка не удалась: счётчик попыток растёт, повтор — не раньше `retry_at`."""
        ...
    def linked_users(self) -> list[User]:
        """Незаблокированные пользователи с привязанным Telegram, по номеру."""
        ...


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    task_id: int
    tracked_link_id: int
    url: str
    region_code: str
    store_host: str
    parser_key: str
    fetch_method: str | None
    min_delay_ms: int
    lease_until: datetime
    # У ссылки ещё нет фото: worker скачает его вместе с проверкой.
    need_image: bool


@dataclass(frozen=True, slots=True)
class LeasedTask:
    task_id: int
    run_id: int
    tracked_link_id: int
    store_id: int
    region_code: str


@dataclass(frozen=True, slots=True)
class RunProgress:
    run_id: int
    kind: str
    status: str
    total: int
    done: int
    failed: int


@dataclass(frozen=True, slots=True)
class RunInfo:
    progress: RunProgress
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExpiredTask:
    task: LeasedTask
    attempt: int
    # Запуск остановлен администратором: задание не возвращается в очередь.
    run_stopping: bool


@dataclass(frozen=True, slots=True)
class FinishedRun:
    run_id: int
    kind: str
    created_at: datetime
    finished_at: datetime


class QueueRepository(Protocol):
    def enqueue_on_add(self, tracked_link_id: int, now: datetime) -> int:
        """Срочное задание на одну ссылку (запуск вида on_add); уже есть активное — его номер."""
        ...
    def active_task(self, tracked_link_id: int) -> int | None:
        """Ждущее или выданное задание ссылки, если есть."""
        ...
    def cancel_queued(self, task_id: int, now: datetime) -> bool:
        """Отменить задание, которое worker ещё не взял; запуск без активных заданий завершается. False — уже взято или закрыто."""
        ...
    def claim(self, worker_id: int, now: datetime, lease_until: datetime) -> ClaimedTask | None:
        """Задание в аренду: самое срочное из магазинов, которые включены, не на паузе и не заняты другим заданием."""
        ...
    def extend(self, worker_id: int, task_id: int, now: datetime, lease_until: datetime) -> bool:
        """Продлить аренду своего действующего задания; иначе False."""
        ...
    def leased(self, worker_id: int, task_id: int, now: datetime) -> LeasedTask | None:
        """Задание этого worker, аренда которого ещё не истекла (блокируется до конца транзакции)."""
        ...
    def finish(self, task: LeasedTask, failed: bool, error_code: str | None, duration_ms: int | None, now: datetime) -> None:
        """Закрыть задание и учесть его в запуске; активных заданий у запуска не осталось — запуск завершён."""
        ...
    def start_run(self, kind: str, slot_key: str | None, requested_by: int | None, now: datetime) -> int | None:
        """Новый плановый или ручной запуск; None — уже идёт другой или слот занят (одновременная вставка учтена)."""
        ...
    def slot_taken(self, slot_key: str) -> bool: ...
    def active_run(self) -> RunProgress | None:
        """Идущий плановый или ручной запуск."""
        ...
    def latest_run(self) -> RunInfo | None:
        """Последний плановый или ручной запуск (идущий или завершённый)."""
        ...
    def runs_to_summarize(self, limit: int) -> list[FinishedRun]:
        """Завершённые плановые и ручные запуски без сводки, по номеру; строки блокируются (второй планировщик их пропустит)."""
        ...
    def mark_summarized(self, run_id: int, now: datetime) -> None: ...
    def enqueue_run(self, run_id: int, purchased_since: datetime, now: datetime) -> RunProgress:
        """Задания запуска: ссылки действующих привязок активных товаров и купленных позже `purchased_since`,
        у живых пользователей и включённых магазинов; ссылка с активным заданием пропускается. Пусто — запуск завершён."""
        ...
    def stop_run(self, run_id: int, now: datetime) -> RunProgress | None:
        """Ждущие задания отменяются, выданные дорабатывают; None — такого идущего запуска нет."""
        ...
    def expired(self, now: datetime) -> list[ExpiredTask]:
        """Выданные задания с истёкшей арендой (блокируются до конца транзакции)."""
        ...
    def requeue(self, task_id: int) -> None: ...
    def wait_paused(self, task: LeasedTask) -> None:
        """Вернуть задание в очередь до конца паузы магазина; попыткой это не считается."""
        ...
    def finish_exhausted(self, task: LeasedTask, error_code: str, duration_ms: int | None, now: datetime) -> int:
        """Паузы магазина исчерпаны: закрыть задание и все ждущие задания его магазина этой ошибкой,
        учесть их в запусках; число закрытых ждущих."""
        ...
    def cancel(self, task: LeasedTask, now: datetime) -> None:
        """Отменить задание остановленного запуска; активных не осталось — запуск завершён."""
        ...


@dataclass(frozen=True, slots=True)
class BellNotification:
    notification_id: int
    kind: str
    payload: dict[str, object]
    created_at: datetime
    read: bool
    repeat_count: int


class NotificationRepository(Protocol):
    def notify_admins(self, kind: str, dedup_key: str, payload: dict[str, object], now: datetime, *, telegram: bool = True) -> int:
        """Уведомление каждому действующему администратору: в колокольчик и в Telegram, если он привязан
        (telegram=False — только колокольчик: например, когда молчит сам бот).
        Повтор с тем же dedup_key не создаёт новых строк, а поднимает счётчик повторов. Число затронутых строк."""
        ...
    def notify_telegram(self, user_id: int, kind: str, dedup_key: str, payload: dict[str, object], now: datetime) -> None:
        """Telegram-сообщение одному пользователю; строка с тем же dedup_key уже есть — ничего не меняется."""
        ...
    def bell(self, user_id: int, offset: int, limit: int) -> list[BellNotification]:
        """Уведомления колокольчика пользователя, сначала новые (по последнему повтору)."""
        ...
    def unread_count(self, user_id: int) -> int: ...
    def mark_read(self, user_id: int, notification_ids: Sequence[int] | None, now: datetime) -> int:
        """Отметить прочитанными свои уведомления колокольчика (None — все); чужие номера не затрагиваются."""
        ...


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Запись журнала; `actor_id` None — действие системы (например, автоматическое закрытие подозрения)."""

    actor_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    before: dict[str, object] | None
    after: dict[str, object] | None
    at: datetime


class AuditRepository(Protocol):
    def add(self, event: AuditEvent) -> None: ...
    def recent(self, actor_id: int, action: str, since: datetime) -> list[datetime]:
        """Моменты действий пользователя этого вида позже `since`, по возрастанию (для лимитов)."""
        ...
    def last(self, action: str, entity_type: str, entity_id: int) -> "AuditEvent | None":
        """Последняя запись этого вида о записи."""
        ...


class DecisionRepository(Protocol):
    def add(self, user_id: int, observation_id: int, decision: Decision, now: datetime) -> int:
        """Решение пользователя; уже есть решение по этому наблюдению — VERSION_CONFLICT."""
        ...
    def delete(self, user_id: int, decision_id: int) -> tuple[int, Decision] | None:
        """Удалить своё решение («Отменить»): наблюдение и каким было решение; чужое или нет — None."""
        ...


@dataclass(frozen=True, slots=True)
class ClientErrorReport:
    """Отчёт приложения, уже очищенный от секретов."""

    error_id: str
    user_id: int
    device_id: int
    app_version: str
    os_version: str
    screen: str
    error_code: str
    message: str
    occurred_at: datetime
    received_at: datetime


class ClientErrorRepository(Protocol):
    def add(self, report: ClientErrorReport) -> None: ...
    def recent(self, device_id: int, since: datetime) -> list[datetime]:
        """Когда устройство присылало отчёты позже `since`, по возрастанию (для лимита)."""
        ...
    def purge(self, received_before: datetime) -> int: ...


class BackupError(Exception):
    """Резервная копия не создана или старые не удалены; код — для журнала копий и уведомления, без подробностей среды."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BackupOutcome:
    """Итог попытки копии: ok — файл и размер; failed — код ошибки (файл есть, если не удалось только удалить старые)."""

    status: str
    file_name: str | None
    size_bytes: int | None
    error_code: str | None


class BackupRepository(Protocol):
    def start(self, now: datetime) -> int:
        """Записать начало попытки; её номер."""
        ...
    def finish(self, backup_id: int, outcome: BackupOutcome, now: datetime) -> None: ...


class BackupArchive(Protocol):
    """Папка копий на домашнем узле."""

    def create(self, name: str) -> int:
        """Зашифрованная копия базы и картинок под этим именем; размер в байтах.
        Сбой — BackupError, и файла с этим именем нет."""
        ...
    def names(self) -> list[str]:
        """Имена файлов в папке копий."""
        ...
    def remove(self, name: str) -> None:
        """Удалить копию; сбой — BackupError."""
        ...


class ImageStore(Protocol):
    def save(self, data: bytes) -> str:
        """Проверить и сохранить фото от worker; путь относительно каталога картинок.
        Неверный файл — VALIDATION_ERROR по полю image."""
        ...


@dataclass(frozen=True, slots=True)
class StoredSetting:
    value: object
    version: int


class SettingRepository(Protocol):
    def get(self, key: str) -> object | None:
        """Значение настройки (JSON) или None, если её не задавали."""
        ...
    def read(self, key: str) -> StoredSetting | None:
        """Значение с версией записи; None — настройку не задавали."""
        ...
    def write(self, key: str, value: object, version: int, actor_id: int, now: datetime) -> int:
        """Записать по версии (0 — настройки ещё нет) и вернуть новую версию; чужая правка — VERSION_CONFLICT."""
        ...


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
    personal_data: PersonalDataRepository
    stores: StoreRepository
    tracked_links: TrackedLinkRepository
    item_links: ItemLinkRepository
    observations: ObservationRepository
    queue: QueueRepository
    workers: WorkerRepository
    price_data: PriceDataRepository
    idempotency: IdempotencyRepository
    settings: SettingRepository
    notifications: NotificationRepository
    decisions: DecisionRepository
    audit: AuditRepository
    client_errors: ClientErrorRepository
    services: ServiceRepository
    telegram: TelegramRepository
    backups: BackupRepository

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def commit(self) -> None: ...
