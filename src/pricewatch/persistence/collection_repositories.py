"""Репозитории сбора цен и очереди на PostgreSQL. Правила — в pricewatch.application.ports.

Одновременное добавление одной страницы или одного магазина разными пользователями не создаёт
дублей: вставка `ON CONFLICT DO NOTHING` по уникальному индексу, затем чтение существующей строки.
Срочное задание ставится так же — частичный уникальный индекс держит одно активное задание на ссылку.
"""

from datetime import datetime

from collections.abc import Sequence

from sqlalchemy import BigInteger, Connection, DateTime, Select, Table, exists, func, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert

from pricewatch.application.ports import (
    ClaimedTask,
    ExpiredTask,
    ItemLinkRecord,
    LeasedTask,
    LinkTarget,
    NewObservation,
    PendingObservation,
    RunInfo,
    RunProgress,
    StorePause,
    StoreRecord,
    WorkerAuth,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.persistence.models import (
    CheckTaskRow,
    ItemLinkRow,
    ItemRow,
    NotificationRow,
    ParseRunRow,
    PriceObservationRow,
    SettingRow,
    StoreRow,
    TrackedLinkRow,
    UserRow,
    WorkerRow,
)

stores: Table = StoreRow.__table__
tracked_links: Table = TrackedLinkRow.__table__
item_links: Table = ItemLinkRow.__table__
items: Table = ItemRow.__table__
parse_runs: Table = ParseRunRow.__table__
check_tasks: Table = CheckTaskRow.__table__
price_observations: Table = PriceObservationRow.__table__
workers: Table = WorkerRow.__table__
users: Table = UserRow.__table__
settings: Table = SettingRow.__table__
notifications: Table = NotificationRow.__table__
SITE_NAME_MAX = 100
PALETTE_SIZE = 8
ACTIVE_TASK = check_tasks.c.status.in_(("queued", "leased"))
# Условие частичного индекса для ON CONFLICT — буквально, как в индексе: с параметрами ($1, $2)
# PostgreSQL не сопоставит его с индексом, а psycopg переходит на параметры после пяти одинаковых запросов.
ACTIVE_TASK_INDEX_WHERE = text("status IN ('queued', 'leased')")
ON_ADD_PRIORITY = 100
ACTIVE_RUN_STATUSES = ("queued", "running", "stopping")


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

    def learn(self, store_id: int, fetch_method: str | None, site_name: str | None) -> None:
        if fetch_method is not None:
            self.connection.execute(update(stores).where(stores.c.id == store_id).values(fetch_method=fetch_method))
        if site_name:
            # Название с сайта заменяет только временное имя сайта у созданного автоматически магазина.
            self.connection.execute(
                update(stores)
                .where(stores.c.id == store_id, stores.c.auto_created.is_(True), stores.c.display_name == stores.c.canonical_host)
                .values(display_name=site_name[:SITE_NAME_MAX])
            )

    def pause_state(self, store_id: int) -> StorePause:
        row = self.connection.execute(
            select(stores.c.id, stores.c.display_name, stores.c.canonical_host, stores.c.pause_reason, stores.c.pause_level)
            .where(stores.c.id == store_id)
            .with_for_update()
        ).one()
        return StorePause(row.id, row.display_name, row.canonical_host, row.pause_reason, row.pause_level)

    def pause(self, store_id: int, reason: str, level: int, until: datetime) -> None:
        self.connection.execute(update(stores).where(stores.c.id == store_id).values(paused_until=until, pause_reason=reason, pause_level=level))

    def clear_pause(self, store_id: int) -> None:
        self.connection.execute(
            update(stores)
            .where(stores.c.id == store_id, (stores.c.pause_level != 0) | stores.c.paused_until.is_not(None))
            .values(paused_until=None, pause_reason=None, pause_level=0)
        )

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


    def baseline_title(self, tracked_link_id: int) -> str | None:
        return self.connection.execute(
            select(tracked_links.c.baseline_title).where(tracked_links.c.id == tracked_link_id)
        ).scalar_one()

    def set_baseline_title(self, tracked_link_id: int, title: str) -> None:
        self.connection.execute(update(tracked_links).where(tracked_links.c.id == tracked_link_id).values(baseline_title=title))

    def set_image_if_missing(self, tracked_link_id: int, image_path: str) -> bool:
        updated = self.connection.execute(
            update(tracked_links).where(tracked_links.c.id == tracked_link_id, tracked_links.c.image_path.is_(None)).values(image_path=image_path)
        )
        return updated.rowcount == 1


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

    def active_binding(self, user_id: int, item_id: int, item_link_id: int) -> ItemLinkRecord | None:
        row = self.connection.execute(
            select(item_links).where(
                item_links.c.id == item_link_id, item_links.c.item_id == item_id, item_links.c.user_id == user_id, item_links.c.valid_to.is_(None)
            )
        ).one_or_none()
        return None if row is None else _to_item_link(row)

    def reopen(self, user_id: int, item_link_id: int) -> bool:
        try:
            with self.connection.begin_nested():
                reopened = self.connection.execute(
                    update(item_links)
                    .where(item_links.c.id == item_link_id, item_links.c.user_id == user_id, item_links.c.valid_to.is_not(None))
                    .values(valid_to=None)
                )
        except IntegrityError:
            return False  # действующая ссылка того же магазина уже есть: отмена не может её вытеснить
        return reopened.rowcount == 1

    def clear_include_stale(self, tracked_link_id: int) -> None:
        self.connection.execute(
            update(items)
            .where(
                items.c.include_stale.is_(True),
                items.c.id.in_(select(item_links.c.item_id).where(item_links.c.tracked_link_id == tracked_link_id, item_links.c.valid_to.is_(None))),
            )
            .values(include_stale=False)
        )

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


    def exists_for_task(self, task_id: int) -> bool:
        return self.connection.execute(
            select(price_observations.c.id).where(price_observations.c.task_id == task_id)
        ).first() is not None

    def add(self, observation: NewObservation) -> None:
        self.connection.execute(
            price_observations.insert().values(
                tracked_link_id=observation.tracked_link_id,
                task_id=observation.task_id,
                checked_at=observation.checked_at,
                status=observation.status,
                price_minor=observation.price_minor,
                card_price_minor=observation.card_price_minor,
                availability=observation.availability,
                title=observation.title,
                region_code=observation.region_code,
                confirmation=observation.confirmation,
                source=observation.source,
            )
        )

    def last_confirmed_price(self, tracked_link_id: int) -> int | None:
        row = self.connection.execute(
            select(price_observations.c.price_minor, price_observations.c.card_price_minor)
            .where(
                price_observations.c.tracked_link_id == tracked_link_id,
                price_observations.c.status == "ok",
                price_observations.c.confirmation == "confirmed",
                price_observations.c.availability != "out_of_stock",
            )
            .order_by(price_observations.c.checked_at.desc(), price_observations.c.id.desc())
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        return row.card_price_minor if row.card_price_minor is not None else row.price_minor

    def pending(self, tracked_link_id: int) -> list[PendingObservation]:
        rows = self.connection.execute(
            select(price_observations).where(
                price_observations.c.tracked_link_id == tracked_link_id, price_observations.c.confirmation == "pending"
            )
        )
        return [
            PendingObservation(
                row.id,
                row.title,
                None if row.availability == "out_of_stock" else (row.card_price_minor if row.card_price_minor is not None else row.price_minor),
            )
            for row in rows
        ]

    def reject(self, observation_ids: Sequence[int]) -> None:
        self.connection.execute(
            update(price_observations).where(price_observations.c.id.in_(list(observation_ids))).values(confirmation="rejected")
        )


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

    def claim(self, worker_id: int, now: datetime, lease_until: datetime) -> ClaimedTask | None:
        busy = check_tasks.alias("busy")
        busy_link = tracked_links.alias("busy_link")
        # Магазин занят, если у него есть задание в действующей аренде.
        store_busy = exists(
            select(busy.c.id)
            .select_from(busy.join(busy_link, busy_link.c.id == busy.c.tracked_link_id))
            .where(busy.c.status == "leased", busy.c.lease_until > now, busy_link.c.store_id == stores.c.id)
        )
        row = self.connection.execute(
            select(check_tasks.c.id, check_tasks.c.run_id, tracked_links.c.id.label("link_id"), tracked_links.c.normalized_url,
                   tracked_links.c.region_code, tracked_links.c.image_path, stores.c.canonical_host, stores.c.parser_key, stores.c.fetch_method,
                   stores.c.min_delay_ms)
            .select_from(check_tasks.join(tracked_links, tracked_links.c.id == check_tasks.c.tracked_link_id).join(stores, stores.c.id == tracked_links.c.store_id))
            .where(
                check_tasks.c.status == "queued",
                stores.c.enabled.is_(True),
                (stores.c.paused_until.is_(None)) | (stores.c.paused_until <= now),
                ~store_busy,
            )
            .order_by(check_tasks.c.priority.desc(), check_tasks.c.id)
            .limit(1)
            # Блокируется и магазин: второй worker в это время пропустит его задания, а не возьмёт второе.
            .with_for_update(skip_locked=True, of=[check_tasks, stores])
        ).one_or_none()
        if row is None:
            return None
        self.connection.execute(
            update(check_tasks)
            .where(check_tasks.c.id == row.id)
            .values(status="leased", worker_id=worker_id, lease_until=lease_until, heartbeat_at=now, attempt=check_tasks.c.attempt + 1)
        )
        self.connection.execute(
            update(parse_runs).where(parse_runs.c.id == row.run_id, parse_runs.c.status == "queued").values(status="running", started_at=now)
        )
        return ClaimedTask(row.id, row.link_id, row.normalized_url, row.region_code, row.canonical_host, row.parser_key,
                           row.fetch_method, row.min_delay_ms, lease_until, row.image_path is None)

    def extend(self, worker_id: int, task_id: int, now: datetime, lease_until: datetime) -> bool:
        updated = self.connection.execute(
            update(check_tasks)
            .where(check_tasks.c.id == task_id, check_tasks.c.worker_id == worker_id, check_tasks.c.status == "leased", check_tasks.c.lease_until > now)
            .values(lease_until=lease_until, heartbeat_at=now)
        )
        return updated.rowcount == 1

    def leased(self, worker_id: int, task_id: int, now: datetime) -> LeasedTask | None:
        row = self.connection.execute(
            select(check_tasks.c.id, check_tasks.c.run_id, tracked_links.c.id.label("link_id"), tracked_links.c.store_id, tracked_links.c.region_code)
            .select_from(check_tasks.join(tracked_links, tracked_links.c.id == check_tasks.c.tracked_link_id))
            .where(check_tasks.c.id == task_id, check_tasks.c.worker_id == worker_id, check_tasks.c.status == "leased", check_tasks.c.lease_until >= now)
            .with_for_update(of=check_tasks)
        ).one_or_none()
        return None if row is None else LeasedTask(row.id, row.run_id, row.link_id, row.store_id, row.region_code)

    def finish(self, task: LeasedTask, failed: bool, error_code: str | None, duration_ms: int | None, now: datetime) -> None:
        self.connection.execute(
            update(check_tasks)
            .where(check_tasks.c.id == task.task_id)
            .values(status="failed" if failed else "done", lease_until=None, error_code=error_code, duration_ms=duration_ms, finished_at=now)
        )
        counter = parse_runs.c.failed if failed else parse_runs.c.done
        self.connection.execute(update(parse_runs).where(parse_runs.c.id == task.run_id).values({counter: counter + 1}))
        self._close_if_drained(task.run_id, now)

    def start_run(self, kind: str, slot_key: str | None, requested_by: int | None, now: datetime) -> int | None:
        # Конфликт по любому из уникальных индексов (идущий запуск, занятый слот) — запуск не создаётся.
        return self.connection.execute(
            insert(parse_runs)
            .values(kind=kind, slot_key=slot_key, requested_by=requested_by, status="queued", created_at=now)
            .on_conflict_do_nothing()
            .returning(parse_runs.c.id)
        ).scalar_one_or_none()

    def slot_taken(self, slot_key: str) -> bool:
        return self.connection.execute(select(exists().where(parse_runs.c.slot_key == slot_key))).scalar_one()

    def active_run(self) -> RunProgress | None:
        row = self.connection.execute(
            _progress().where(parse_runs.c.kind.in_(("scheduled", "manual")), parse_runs.c.status.in_(ACTIVE_RUN_STATUSES))
        ).one_or_none()
        return None if row is None else RunProgress(*row)

    def latest_run(self) -> RunInfo | None:
        row = self.connection.execute(
            _progress()
            .add_columns(parse_runs.c.started_at, parse_runs.c.finished_at)
            .where(parse_runs.c.kind.in_(("scheduled", "manual")))
            .order_by(parse_runs.c.id.desc())
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        return RunInfo(RunProgress(row.id, row.kind, row.status, row.total, row.done, row.failed), row.started_at, row.finished_at)

    def enqueue_run(self, run_id: int, purchased_since: datetime, now: datetime) -> RunProgress:
        tracked = (items.c.state == "active") | ((items.c.state == "purchased") & (items.c.purchased_at > purchased_since))
        links = (
            select(item_links.c.tracked_link_id)
            .select_from(
                item_links.join(items, items.c.id == item_links.c.item_id)
                .join(users, users.c.id == items.c.user_id)
                .join(stores, stores.c.id == item_links.c.store_id)
            )
            .where(item_links.c.valid_to.is_(None), items.c.deleted_at.is_(None), users.c.disabled_at.is_(None), stores.c.enabled.is_(True), tracked)
            .distinct()
        )
        added = self.connection.execute(
            insert(check_tasks)
            .from_select(
                ["run_id", "tracked_link_id", "created_at"],
                select(literal(run_id, BigInteger), links.subquery().c.tracked_link_id, literal(now, DateTime(timezone=True))),
            )
            # Ссылку уже проверяет другое задание (например, срочное) — второе не нужно.
            .on_conflict_do_nothing(index_elements=["tracked_link_id"], index_where=ACTIVE_TASK_INDEX_WHERE)
            .returning(check_tasks.c.id)
        ).all()
        self.connection.execute(update(parse_runs).where(parse_runs.c.id == run_id).values(total=len(added)))
        self._close_if_drained(run_id, now)
        return self._progress(run_id)

    def stop_run(self, run_id: int, now: datetime) -> RunProgress | None:
        active = (parse_runs.c.id == run_id) & parse_runs.c.kind.in_(("scheduled", "manual")) & parse_runs.c.status.in_(ACTIVE_RUN_STATUSES)
        if self.connection.execute(select(exists().where(active))).scalar_one() is False:
            return None
        # Порядок блокировок как у выдачи и результата: сначала задания, потом запуск — без взаимной блокировки.
        self.connection.execute(
            update(check_tasks).where(check_tasks.c.run_id == run_id, check_tasks.c.status == "queued").values(status="cancelled", finished_at=now)
        )
        self.connection.execute(update(parse_runs).where(active).values(status="stopping"))
        self._close_if_drained(run_id, now)
        return self._progress(run_id)

    def expired(self, now: datetime) -> list[ExpiredTask]:
        rows = self.connection.execute(
            select(check_tasks.c.id, check_tasks.c.run_id, check_tasks.c.attempt, tracked_links.c.id.label("link_id"),
                   tracked_links.c.store_id, tracked_links.c.region_code, parse_runs.c.status.label("run_status"))
            .select_from(
                check_tasks.join(tracked_links, tracked_links.c.id == check_tasks.c.tracked_link_id)
                .join(parse_runs, parse_runs.c.id == check_tasks.c.run_id)
            )
            .where(check_tasks.c.status == "leased", check_tasks.c.lease_until < now)
            .order_by(check_tasks.c.id)
            # Блокируется и запуск, чтобы остановка не прошла между чтением и возвратом в очередь. Занятое
            # (по заданию прямо сейчас принимается результат или запуск меняют) пропускается до следующего обхода.
            .with_for_update(skip_locked=True, of=[check_tasks, parse_runs])
        ).all()
        return [
            ExpiredTask(LeasedTask(row.id, row.run_id, row.link_id, row.store_id, row.region_code), row.attempt, row.run_status == "stopping")
            for row in rows
        ]

    def requeue(self, task_id: int) -> None:
        self.connection.execute(
            update(check_tasks).where(check_tasks.c.id == task_id).values(status="queued", worker_id=None, lease_until=None, heartbeat_at=None)
        )

    def wait_paused(self, task: LeasedTask) -> None:
        # Выдача прибавила попытку; пауза магазина — не сбой worker, попытка возвращается.
        self.connection.execute(
            update(check_tasks)
            .where(check_tasks.c.id == task.task_id)
            .values(status="queued", worker_id=None, lease_until=None, heartbeat_at=None, attempt=check_tasks.c.attempt - 1)
        )

    def finish_exhausted(self, task: LeasedTask, error_code: str, duration_ms: int | None, now: datetime) -> int:
        queued = self.connection.execute(
            select(check_tasks.c.id, check_tasks.c.run_id)
            .where(
                check_tasks.c.status == "queued",
                check_tasks.c.tracked_link_id.in_(select(tracked_links.c.id).where(tracked_links.c.store_id == task.store_id)),
            )
            .order_by(check_tasks.c.id)
            .with_for_update(of=check_tasks)
        ).all()
        per_run: dict[int, int] = {}
        for row in queued:
            per_run[row.run_id] = per_run.get(row.run_id, 0) + 1
        run_ids = sorted(per_run.keys() | {task.run_id})
        # Запуски блокируются по возрастанию номера: два таких результата одновременно не ждут друг друга по кругу.
        self.connection.execute(select(parse_runs.c.id).where(parse_runs.c.id.in_(run_ids)).order_by(parse_runs.c.id).with_for_update())
        if queued:
            self.connection.execute(
                update(check_tasks).where(check_tasks.c.id.in_([row.id for row in queued])).values(status="failed", error_code=error_code, finished_at=now)
            )
        for run_id, count in per_run.items():
            self.connection.execute(update(parse_runs).where(parse_runs.c.id == run_id).values(failed=parse_runs.c.failed + count))
        self.finish(task, True, error_code, duration_ms, now)
        for run_id in run_ids:
            self._close_if_drained(run_id, now)
        return len(queued)

    def cancel(self, task: LeasedTask, now: datetime) -> None:
        self.connection.execute(
            update(check_tasks).where(check_tasks.c.id == task.task_id).values(status="cancelled", lease_until=None, finished_at=now)
        )
        self._close_if_drained(task.run_id, now)

    def _close_if_drained(self, run_id: int, now: datetime) -> None:
        # Активных заданий не осталось — запуск завершён (в том числе остановленный и пустой).
        self.connection.execute(
            update(parse_runs)
            .where(
                parse_runs.c.id == run_id,
                parse_runs.c.status.in_(ACTIVE_RUN_STATUSES),
                ~exists().where(check_tasks.c.run_id == run_id, ACTIVE_TASK),
            )
            .values(status="finished", finished_at=now)
        )

    def _progress(self, run_id: int) -> RunProgress:
        return RunProgress(*self.connection.execute(_progress().where(parse_runs.c.id == run_id)).one())

    def _active_task(self, tracked_link_id: int) -> int | None:
        return self.connection.execute(
            select(check_tasks.c.id).where(check_tasks.c.tracked_link_id == tracked_link_id, ACTIVE_TASK)
        ).scalar_one_or_none()


class SqlWorkerRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create(self, name: str, token_hash: str, now: datetime) -> int:
        try:
            with self.connection.begin_nested():
                return self.connection.execute(
                    workers.insert().values(name=name, token_hash=token_hash, created_at=now).returning(workers.c.id)
                ).scalar_one()
        except IntegrityError as error:
            if "uq_workers_name" in str(error.orig):
                raise PriceWatcherError(ErrorCode.NAME_TAKEN) from error
            raise

    def find_by_token(self, token_hash: str) -> WorkerAuth | None:
        row = self.connection.execute(
            select(workers.c.id, workers.c.disabled_at, workers.c.next_token_hash).where(
                (workers.c.token_hash == token_hash) | (workers.c.next_token_hash == token_hash)
            )
        ).one_or_none()
        if row is None:
            return None
        return WorkerAuth(row.id, row.disabled_at is not None, row.next_token_hash == token_hash)

    def promote_next_token(self, worker_id: int) -> None:
        self.connection.execute(
            update(workers)
            .where(workers.c.id == worker_id, workers.c.next_token_hash.is_not(None))
            .values(token_hash=workers.c.next_token_hash, next_token_hash=None)
        )

    def set_next_token(self, name: str, token_hash: str) -> None:
        updated = self.connection.execute(update(workers).where(workers.c.name == name).values(next_token_hash=token_hash))
        if updated.rowcount != 1:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)

    def touch(self, worker_id: int, now: datetime) -> None:
        self.connection.execute(update(workers).where(workers.c.id == worker_id).values(last_seen_at=now))


class SqlSettingRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def get(self, key: str) -> object | None:
        return self.connection.execute(select(settings.c.value_json).where(settings.c.key == key)).scalar_one_or_none()


class SqlNotificationRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def notify_admins(self, kind: str, dedup_key: str, payload: dict[str, object], now: datetime) -> int:
        admins = self.connection.execute(
            select(users.c.id, users.c.telegram_chat_id).where(users.c.role == "admin", users.c.disabled_at.is_(None)).order_by(users.c.id)
        ).all()
        rows = [
            {"user_id": admin.id, "channel": channel, "kind": kind, "dedup_key": dedup_key, "payload_json": payload, "created_at": now}
            for admin in admins
            for channel in ("in_app", "telegram")
            if channel == "in_app" or admin.telegram_chat_id is not None
        ]
        if not rows:
            return 0
        created = self.connection.execute(
            insert(notifications).values(rows).on_conflict_do_nothing(index_elements=["user_id", "channel", "dedup_key"]).returning(notifications.c.id)
        ).all()
        return len(created)


def _progress() -> Select:
    return select(parse_runs.c.id, parse_runs.c.kind, parse_runs.c.status, parse_runs.c.total, parse_runs.c.done, parse_runs.c.failed)


def _to_item_link(row: object) -> ItemLinkRecord:
    return ItemLinkRecord(row.id, row.item_id, row.tracked_link_id, row.store_id, row.valid_from)  # type: ignore[attr-defined]
