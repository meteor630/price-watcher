"""Репозитории сбора цен и очереди на PostgreSQL. Правила — в pricewatch.application.ports.

Одновременное добавление одной страницы или одного магазина разными пользователями не создаёт
дублей: вставка `ON CONFLICT DO NOTHING` по уникальному индексу, затем чтение существующей строки.
Срочное задание ставится так же — частичный уникальный индекс держит одно активное задание на ссылку.
"""

from datetime import datetime

from sqlalchemy import Connection, Table, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from pricewatch.application.ports import ItemLinkRecord, LinkTarget, StoreRecord
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.models import (
    CheckTaskRow,
    ItemLinkRow,
    ItemRow,
    ParseRunRow,
    PriceObservationRow,
    StoreRow,
    TrackedLinkRow,
)

stores: Table = StoreRow.__table__
tracked_links: Table = TrackedLinkRow.__table__
item_links: Table = ItemLinkRow.__table__
items: Table = ItemRow.__table__
parse_runs: Table = ParseRunRow.__table__
check_tasks: Table = CheckTaskRow.__table__
price_observations: Table = PriceObservationRow.__table__
PALETTE_SIZE = 8
ACTIVE_TASK = check_tasks.c.status.in_(("queued", "leased"))
# Условие частичного индекса для ON CONFLICT — буквально, как в индексе: с параметрами ($1, $2)
# PostgreSQL не сопоставит его с индексом, а psycopg переходит на параметры после пяти одинаковых запросов.
ACTIVE_TASK_INDEX_WHERE = text("status IN ('queued', 'leased')")
ON_ADD_PRIORITY = 100


class SqlStoreRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def get_or_create(self, target: LinkTarget, now: datetime) -> StoreRecord:
        existing = self._find(target.store_host)
        if existing is not None:
            return existing
        # Цвета палитры раздаются по кругу в порядке появления магазинов.
        count = self.connection.execute(select(func.count()).select_from(stores)).scalar_one()
        self.connection.execute(
            insert(stores)
            .values(
                canonical_host=target.store_host,
                display_name=target.store_name,
                color=f"store-{count % PALETTE_SIZE + 1}",
                has_card_price=target.has_card_price,
                parser_key=target.parser_key,
                auto_created=target.auto_created,
                min_delay_ms=target.min_delay_ms,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["canonical_host"])
        )
        created = self._find(target.store_host)
        if created is None:
            raise RuntimeError("магазин не найден сразу после вставки")
        return created

    def _find(self, host: str) -> StoreRecord | None:
        row = self.connection.execute(
            select(stores.c.id, stores.c.canonical_host, stores.c.display_name, stores.c.parser_key, stores.c.enabled).where(
                stores.c.canonical_host == host
            )
        ).one_or_none()
        return None if row is None else StoreRecord(row.id, row.canonical_host, row.display_name, row.parser_key, row.enabled)


class SqlTrackedLinkRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def get_or_create(self, store_id: int, normalized_url: str, region_code: str, now: datetime) -> int:
        self.connection.execute(
            insert(tracked_links)
            .values(store_id=store_id, normalized_url=normalized_url, region_code=region_code, created_at=now)
            .on_conflict_do_nothing(index_elements=["normalized_url", "region_code"])
        )
        return self.connection.execute(
            select(tracked_links.c.id).where(
                tracked_links.c.normalized_url == normalized_url, tracked_links.c.region_code == region_code
            )
        ).scalar_one()


class SqlItemLinkRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def lock_item(self, user_id: int, item_id: int) -> None:
        found = self.connection.execute(
            select(items.c.id)
            .where(items.c.id == item_id, items.c.user_id == user_id, items.c.deleted_at.is_(None))
            .with_for_update()
        ).one_or_none()
        if found is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)

    def active_for_store(self, item_id: int, store_id: int) -> ItemLinkRecord | None:
        row = self.connection.execute(
            select(item_links).where(
                item_links.c.item_id == item_id, item_links.c.store_id == store_id, item_links.c.valid_to.is_(None)
            )
        ).one_or_none()
        return None if row is None else _to_item_link(row)

    def bind(self, user_id: int, item_id: int, tracked_link_id: int, store_id: int, now: datetime) -> ItemLinkRecord:
        row = self.connection.execute(
            insert(item_links)
            .values(user_id=user_id, item_id=item_id, tracked_link_id=tracked_link_id, store_id=store_id, valid_from=now)
            .returning(item_links)
        ).one()
        return _to_item_link(row)

    def close(self, item_link_id: int, now: datetime) -> None:
        self.connection.execute(update(item_links).where(item_links.c.id == item_link_id).values(valid_to=now))

    def added_since(self, user_id: int, since: datetime) -> list[datetime]:
        return list(
            self.connection.execute(
                select(item_links.c.valid_from)
                .where(item_links.c.user_id == user_id, item_links.c.valid_from > since)
                .order_by(item_links.c.valid_from)
            ).scalars()
        )


class SqlObservationRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def has_confirmed_success_since(self, tracked_link_id: int, since: datetime) -> bool:
        found = self.connection.execute(
            select(price_observations.c.id)
            .where(
                price_observations.c.tracked_link_id == tracked_link_id,
                price_observations.c.status == "ok",
                price_observations.c.confirmation == "confirmed",
                price_observations.c.checked_at >= since,
            )
            .limit(1)
        ).first()
        return found is not None


class SqlQueueRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def enqueue_on_add(self, tracked_link_id: int, now: datetime) -> int:
        existing = self._active_task(tracked_link_id)
        if existing is not None:
            return existing
        run_id = self.connection.execute(
            insert(parse_runs).values(kind="on_add", status="queued", total=1, created_at=now).returning(parse_runs.c.id)
        ).scalar_one()
        task_id = self.connection.execute(
            insert(check_tasks)
            .values(run_id=run_id, tracked_link_id=tracked_link_id, priority=ON_ADD_PRIORITY, created_at=now)
            .on_conflict_do_nothing(index_elements=["tracked_link_id"], index_where=ACTIVE_TASK_INDEX_WHERE)
            .returning(check_tasks.c.id)
        ).scalar_one_or_none()
        if task_id is not None:
            return task_id
        # Другая транзакция успела поставить задание на эту ссылку: лишний запуск не нужен.
        self.connection.execute(parse_runs.delete().where(parse_runs.c.id == run_id))
        concurrent = self._active_task(tracked_link_id)
        if concurrent is None:
            raise RuntimeError("активное задание не найдено после конфликта вставки")
        return concurrent

    def _active_task(self, tracked_link_id: int) -> int | None:
        return self.connection.execute(
            select(check_tasks.c.id).where(check_tasks.c.tracked_link_id == tracked_link_id, ACTIVE_TASK)
        ).scalar_one_or_none()


def _to_item_link(row: object) -> ItemLinkRecord:
    return ItemLinkRecord(row.id, row.item_id, row.tracked_link_id, row.store_id, row.valid_from)  # type: ignore[attr-defined]
