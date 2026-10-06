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
from pricewatch.domain.pricing import Observation
from pricewatch.domain.shopping import Category, Group, Item, ItemState


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


class PriceDataRepository(Protocol):
    def items(self, user_id: int) -> list[PricedItem]:
        """Неудалённые товары в неудалённых категориях и группах, в порядке категорий и позиций."""
        ...
    def bindings(self, user_id: int) -> list[LinkBinding]:
        """Все привязки ссылок к этим товарам, в том числе закрытые: история цены не обрывается."""
        ...
    def stores(self, store_ids: Sequence[int]) -> dict[int, StoreInfo]: ...
    def links(self, tracked_link_ids: Sequence[int]) -> dict[int, LinkInfo]:
        """Адрес страницы в магазине и путь сохранённого фото ссылки."""
        ...
    def observations(self, tracked_link_ids: Sequence[int], since: datetime) -> dict[int, list[Observation]]:
        """Наблюдения ссылок, нужные для расчёта на любой момент начиная с `since`: всё с `since`
        и раньше — начиная с последнего успешного подтверждённого до `since` (или последнего любого,
        если успешных не было). Более старая история на такие расчёты не влияет."""
        ...
    def pending_reviews(self) -> int:
        """Число изменений, ждущих решения администратора."""
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


@dataclass(frozen=True, slots=True)
class WorkerAuth:
    worker_id: int
    disabled: bool
    # Worker пришёл с новым токеном ротации — старый пора погасить.
    used_next_token: bool


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


class QueueRepository(Protocol):
    def enqueue_on_add(self, tracked_link_id: int, now: datetime) -> int:
        """Срочное задание на одну ссылку (запуск вида on_add); уже есть активное — его номер."""
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


class NotificationRepository(Protocol):
    def notify_admins(self, kind: str, dedup_key: str, payload: dict[str, object], now: datetime) -> int:
        """Уведомление каждому действующему администратору: в колокольчик и в Telegram, если он привязан.
        Повтор с тем же dedup_key не создаёт новых строк. Число созданных."""
        ...


class ImageStore(Protocol):
    def save(self, data: bytes) -> str:
        """Проверить и сохранить фото от worker; путь относительно каталога картинок.
        Неверный файл — VALIDATION_ERROR по полю image."""
        ...


class SettingRepository(Protocol):
    def get(self, key: str) -> object | None:
        """Значение настройки (JSON) или None, если её не задавали."""
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

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def commit(self) -> None: ...
