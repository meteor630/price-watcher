"""Перенос данных старого сайта (SQLite) в новую базу — один раз (решение владельца 13.10.2026).

Запуск на домашнем узле в контейнере сайта: база и ключи — из его окружения, старая база — только на чтение
(копия в обычном режиме журнала, не WAL: папка подключается только для чтения).
    docker compose run --rm --no-deps -v <папка tools>:/tools:ro -v <папка со старой базой>:/import:ro app \\
        python /tools/import_legacy.py /import/pricewatch.db --login <логин> [--replace]

Переносится владельцу `--login`:
- категории (кроме архивных — в старой базе они пустые), группы, товары с порядком; архивные товары — в архив;
  «сколько покупаю = 0» — количество 1, не входит в итог;
- магазины (по адресу ссылки, как при добавлении на сайте; старое название — вместо временного имени сайта);
- ссылки с окнами действия: даты замены в старой базе — московские дни, поэтому граница между адресами —
  первая проверка нового адреса; адрес, заменённый до первой проверки, не переносится;
- запуски (сводки по ним не отправляются) и проверки с ценой: «ok», «нет в наличии», подозрительные
  (ждут решения; отклонённые тогда — с решением «отклонить»). Проверки без цены (ошибки, «карточка удалена»)
  не переносятся (решение владельца 13.10.2026).
Не переносятся: сессии, состояние старого бота, картинки.

`--replace` — сначала удалить категории владельца так же, как кнопка «Удалить» на сайте; без него перенос
в учётную запись, где уже есть категории, отклоняется. Всё — одной транзакцией: либо целиком, либо ничего.
Повторный перенос отклоняется (признак — запуски с ключом legacy-*): каталог ссылок общий, и история цен задвоилась бы.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import Connection, Engine, exists, insert, select

from pricewatch.application.ports import NewItem
from pricewatch.config.settings import ConfigError, load_settings
from pricewatch.domain.errors import PriceWatcherError
from pricewatch.domain.review import Decision
from pricewatch.domain.shopping import REGION_CODE
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.collection_repositories import SqlStoreRepository, SqlTrackedLinkRepository
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.models import CheckTaskRow, ItemLinkRow, ParseRunRow, PriceObservationRow
from pricewatch.persistence.review_repositories import SqlDecisionRepository
from pricewatch.persistence.shopping_repositories import SqlCategoryRepository, SqlGroupRepository, SqlItemRepository
from pricewatch.persistence.user_repository import SqlUserRepository

item_links = ItemLinkRow.__table__
parse_runs = ParseRunRow.__table__
check_tasks = CheckTaskRow.__table__
price_observations = PriceObservationRow.__table__

# Откуда взята цена: данные сайта или текст страницы.
SOURCES = {"JSON-LD": "site_data", "microdata": "site_data", "meta": "site_data", "на странице": "page"}
# Проверки без цены не переносятся.
WITHOUT_PRICE = frozenset({"error", "gone"})
DECISIONS = {"accepted": Decision.ACCEPT, "rejected": Decision.REJECT}
RUN_KINDS = {"daily": "scheduled", "manual": "manual"}
# Ключ слота перенесённых плановых запусков; по нему же виден уже выполненный перенос.
LEGACY_SLOT = "legacy-"
WAL_MODE = 2


class LegacyError(Exception):
    """Данные старой базы не укладываются в правила переноса: перенос отменён целиком."""


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    name: str
    archived: bool


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    full_name: str | None
    category_id: str
    group_name: str | None
    quantity: int
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class Store:
    id: str
    name: str
    method: str | None


@dataclass(frozen=True, slots=True)
class Listing:
    id: int
    product_id: str
    store_id: str
    state: str
    ref_title: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ListingUrl:
    id: int
    listing_id: int
    url: str
    closed: bool


@dataclass(frozen=True, slots=True)
class Run:
    id: int
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    total: int
    failed: int


@dataclass(frozen=True, slots=True)
class Check:
    id: int
    url_id: int
    run_id: int | None
    checked_at: datetime
    status: str
    price: str | None
    card_price: str | None
    title: str | None
    source: str | None
    decision: str | None


@dataclass(frozen=True, slots=True)
class Legacy:
    categories: list[Category]
    products: list[Product]
    stores: dict[str, Store]
    listings: list[Listing]
    urls: list[ListingUrl]
    runs: list[Run]
    checks: list[Check]


@dataclass(frozen=True, slots=True)
class Window:
    url: ListingUrl
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True, slots=True)
class MappedCheck:
    availability: str
    price_minor: int | None
    card_price_minor: int | None
    title: str | None
    confirmation: str
    source: str | None
    decision: Decision | None


@dataclass(frozen=True, slots=True)
class Summary:
    replaced_categories: int
    categories: int
    groups: int
    items: int
    stores: int
    links: int
    runs: int
    observations: int
    decisions: int


# --- чтение старой базы ---


def read_legacy(path: Path) -> Legacy:
    if not path.is_file():
        raise LegacyError(f"нет файла старой базы: {path}")
    # Байт 18 заголовка SQLite: 2 — режим WAL. Такую базу из папки только для чтения не открыть (нужен файл -shm).
    with path.open("rb") as header:
        if header.read(19)[18:19] == bytes([WAL_MODE]):
            raise LegacyError("старая база в режиме WAL: перенесите копию, переведённую в обычный режим (PRAGMA journal_mode=DELETE)")
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return Legacy(
            categories=[Category(row[0], row[1], row[2] is not None) for row in db.execute(
                "SELECT id, name, archived_at FROM categories ORDER BY position, name")],
            products=[Product(row[0], row[1], row[2], row[3], row[4], row[5], optional_moment(row[6])) for row in db.execute(
                "SELECT p.id, p.name, p.full, p.category_id, p.group_name, p.qty, p.archived_at FROM products p "
                "JOIN categories c ON c.id = p.category_id ORDER BY c.position, p.position, p.created_at, p.id")],
            stores={row[0]: Store(row[0], row[1], row[2]) for row in db.execute("SELECT id, name, method FROM stores")},
            listings=[Listing(row[0], row[1], row[2], row[3], row[4], moment(row[5])) for row in db.execute(
                "SELECT id, product_id, store_id, state, ref_title, created_at FROM listings ORDER BY id")],
            urls=[ListingUrl(row[0], row[1], row[2], row[3] is not None) for row in db.execute(
                "SELECT id, listing_id, url, active_to FROM listing_urls ORDER BY listing_id, active_from, id")],
            runs=[Run(row[0], row[1], moment(row[2]), optional_moment(row[3]), row[4], row[5]) for row in db.execute(
                "SELECT id, trigger, started_at, finished_at, total, failed FROM runs ORDER BY id")],
            # Цены — текстом: в старой базе они REAL, в копейки переводятся точно, без float.
            checks=[Check(row[0], row[1], row[2], moment(row[3]), row[4], row[5], row[6], row[7], row[8], row[9]) for row in db.execute(
                "SELECT id, url_id, run_id, checked_at, status, CAST(price AS TEXT), CAST(card_price AS TEXT), title, source, decision "
                "FROM checks ORDER BY checked_at, id")],
        )
    finally:
        db.close()


def moment(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        raise LegacyError(f"время без часового пояса: {raw}")
    return value.astimezone(UTC)


def optional_moment(raw: str | None) -> datetime | None:
    return None if raw is None else moment(raw)


# --- правила переноса ---


def to_minor(raw: str | None) -> int | None:
    """Рубли из старой базы — в копейки; дробная копейка или неположительная цена — ошибка данных."""
    if raw is None:
        return None
    try:
        kopecks = Decimal(raw) * 100
    except InvalidOperation as error:
        raise LegacyError(f"не число: {raw!r}") from error
    if kopecks != kopecks.to_integral_value() or kopecks <= 0:
        raise LegacyError(f"цена не в целых копейках или не больше нуля: {raw}")
    return int(kopecks)


def map_check(check: Check) -> MappedCheck | None:
    """Проверка в наблюдение новой базы; None — проверка без цены, не переносится."""
    if check.status in WITHOUT_PRICE:
        return None
    if check.status not in ("ok", "out_of_stock", "suspect"):
        raise LegacyError(f"проверка {check.id}: неизвестный статус {check.status!r}")
    if check.source is not None and check.source not in SOURCES:
        raise LegacyError(f"проверка {check.id}: неизвестный источник цены {check.source!r}")
    if check.decision is not None and (check.status != "suspect" or check.decision not in DECISIONS):
        raise LegacyError(f"проверка {check.id}: решение {check.decision!r} при статусе {check.status!r}")
    price = to_minor(check.price)
    if check.status != "out_of_stock" and price is None:
        raise LegacyError(f"проверка {check.id}: статус {check.status!r} без цены")
    return MappedCheck(
        availability="out_of_stock" if check.status == "out_of_stock" else "in_stock",
        price_minor=price,
        card_price_minor=to_minor(check.card_price),
        title=(check.title or "").strip() or None,
        confirmation="pending" if check.status == "suspect" else "confirmed",
        source=None if check.source is None else SOURCES[check.source],
        decision=None if check.decision is None else DECISIONS[check.decision],
    )


def link_windows(listing: Listing, urls: Sequence[ListingUrl], checked: Mapping[int, Sequence[datetime]]) -> list[Window]:
    """Окна действия адресов ссылки. Даты замены в старой базе — дни, поэтому граница — первая проверка нового адреса;
    адрес, закрытый без единой проверки, не переносится."""
    if listing.state not in ("active", "archived"):
        raise LegacyError(f"ссылка {listing.id}: состояние {listing.state!r} не переносится")
    kept = [url for url in urls if checked.get(url.id) or not url.closed]
    if kept and kept[-1].closed:
        raise LegacyError(f"ссылка {listing.id}: последний адрес закрыт, а время закрытия неизвестно")
    starts: list[datetime] = []
    for index, url in enumerate(kept):
        seen = sorted(checked.get(url.id, ()))
        if index == 0:
            starts.append(min([listing.created_at, *seen[:1]]))
        elif seen:
            starts.append(seen[0])
        else:
            raise LegacyError(f"ссылка {listing.id}: адрес {url.id} заменил прежний, но ни разу не проверен — границу не определить")
    windows = [Window(url, start, end) for url, start, end in zip(kept, starts, [*starts[1:], None], strict=True)]
    for window in windows:
        for at in checked.get(window.url.id, ()):
            if at < window.valid_from or (window.valid_to is not None and at >= window.valid_to):
                raise LegacyError(f"ссылка {listing.id}: проверка адреса {window.url.id} в {at.isoformat()} вне его окна")
        if window.valid_to is not None and window.valid_to <= window.valid_from:
            raise LegacyError(f"ссылка {listing.id}: пустое окно адреса {window.url.id}")
    return windows


def run_values(run: Run, owner_id: int, done: int) -> dict[str, object]:
    if run.trigger not in RUN_KINDS:
        raise LegacyError(f"запуск {run.id}: неизвестный тип {run.trigger!r}")
    if run.finished_at is None:
        raise LegacyError(f"запуск {run.id} не завершён")
    kind = RUN_KINDS[run.trigger]
    return {
        "kind": kind,
        # Ключ слота не совпадёт с ключами планировщика (дата и время): плановый запуск не будет считаться уже прошедшим.
        "slot_key": f"{LEGACY_SLOT}{run.id}" if kind == "scheduled" else None,
        "status": "finished",
        "requested_by": owner_id if kind == "manual" else None,
        "total": max(run.total, done + run.failed),
        "done": done,
        "failed": run.failed,
        "created_at": run.started_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "summarized_at": run.finished_at,
    }


# --- запись в новую базу ---


def import_legacy(engine: Engine, legacy: Legacy, login: str, replace: bool, now: datetime) -> Summary:
    with engine.begin() as db:
        credentials = SqlUserRepository(db).find_credentials(login)
        if credentials is None:
            raise LegacyError(f"нет пользователя с логином {login!r}")
        owner_id = credentials.user.id
        if db.execute(select(exists().where(parse_runs.c.slot_key.like(f"{LEGACY_SLOT}%")))).scalar_one():
            raise LegacyError("перенос уже выполнялся: повторный задвоил бы историю цен")
        categories = SqlCategoryRepository(db)
        existing = categories.list_active(owner_id)
        if existing and not replace:
            raise LegacyError(f"у {login!r} уже есть категории ({len(existing)}): перенос только в пустую учётную запись или с --replace")
        for category in existing:
            categories.delete(owner_id, category.id, category.version)

        category_ids = {category.id: categories.create(owner_id, category.name).id for category in legacy.categories if not category.archived}
        item_ids, group_count = _items(db, owner_id, legacy.products, category_ids)
        windows, store_count = _links(db, owner_id, legacy, item_ids, now)
        run_ids, observation_count, decision_count = _history(db, owner_id, legacy, windows, now)
    return Summary(len(existing), len(category_ids), group_count, len(item_ids), store_count, len(windows), len(run_ids),
                   observation_count, decision_count)


def _items(db: Connection, owner_id: int, products: Sequence[Product], category_ids: Mapping[str, int]) -> tuple[dict[str, int], int]:
    groups = SqlGroupRepository(db)
    items = SqlItemRepository(db)
    group_ids: dict[tuple[int, str], int] = {}
    item_ids: dict[str, int] = {}
    for product in products:
        if product.category_id not in category_ids:
            raise LegacyError(f"товар {product.id} в архивной категории {product.category_id}")
        category_id = category_ids[product.category_id]
        group_id = None
        if product.group_name:
            key = (category_id, product.group_name)
            if key not in group_ids:
                group_ids[key] = groups.create(owner_id, category_id, product.group_name).id
            group_id = group_ids[key]
        item = items.create(owner_id, NewItem(
            category_id=category_id, name=product.name, group_id=group_id, full_name=product.full_name,
            quantity=max(product.quantity, 1), in_total=product.quantity > 0,
        ))
        if product.archived_at is not None:
            items.archive(owner_id, item.id, item.version, product.archived_at)
        item_ids[product.id] = item.id
    return item_ids, len(group_ids)


def _links(db: Connection, owner_id: int, legacy: Legacy, item_ids: Mapping[str, int], now: datetime) -> tuple[dict[int, int], int]:
    """Номер старого адреса → ссылка каталога (для истории) и число магазинов."""
    catalog = LinkCatalog()
    stores = SqlStoreRepository(db)
    tracked = SqlTrackedLinkRepository(db)
    urls_by_listing: dict[int, list[ListingUrl]] = defaultdict(list)
    for url in legacy.urls:
        urls_by_listing[url.listing_id].append(url)
    checked: dict[int, list[datetime]] = defaultdict(list)
    for check in legacy.checks:
        if map_check(check) is not None:
            checked[check.url_id].append(check.checked_at)

    targets: dict[int, int] = {}
    store_ids: set[int] = set()
    for listing in legacy.listings:
        old_store = legacy.stores[listing.store_id]
        for window in link_windows(listing, urls_by_listing[listing.id], checked):
            target = catalog.resolve(window.url.url)
            store = stores.get_or_create(target, now)
            if not store.enabled:
                raise LegacyError(f"магазин {store.canonical_host} отключён администратором")
            # Старое название заменяет только временное имя сайта у созданного автоматически магазина.
            stores.learn(store.id, old_store.method, old_store.name)
            store_ids.add(store.id)
            tracked_link_id = tracked.get_or_create(store.id, target.normalized_url, REGION_CODE, now)
            if listing.ref_title and tracked.baseline_title(tracked_link_id) is None:
                tracked.set_baseline_title(tracked_link_id, listing.ref_title)
            db.execute(insert(item_links).values(
                user_id=owner_id, item_id=item_ids[listing.product_id], tracked_link_id=tracked_link_id, store_id=store.id,
                valid_from=window.valid_from, valid_to=window.valid_to,
            ))
            targets[window.url.id] = tracked_link_id
    return targets, len(store_ids)


def _history(db: Connection, owner_id: int, legacy: Legacy, links: Mapping[int, int], now: datetime) -> tuple[dict[int | None, int], int, int]:
    decisions = SqlDecisionRepository(db)
    mapped = [(check, found) for check in legacy.checks if (found := map_check(check)) is not None]
    done: dict[int | None, int] = defaultdict(int)
    for check, _ in mapped:
        done[check.run_id] += 1

    run_ids: dict[int | None, int] = {}
    for run in legacy.runs:
        run_ids[run.id] = db.execute(insert(parse_runs).values(**run_values(run, owner_id, done[run.id])).returning(parse_runs.c.id)).scalar_one()
    orphans = [check.checked_at for check, _ in mapped if check.run_id is None]
    if orphans:
        # Проверки без запуска — при добавлении ссылки: один запуск on_add на все.
        run_ids[None] = db.execute(insert(parse_runs).values(
            kind="on_add", status="finished", total=len(orphans), done=len(orphans), failed=0,
            created_at=min(orphans), started_at=min(orphans), finished_at=max(orphans), summarized_at=max(orphans),
        ).returning(parse_runs.c.id)).scalar_one()

    decision_count = 0
    for check, found in mapped:
        if check.url_id not in links:
            raise LegacyError(f"проверка {check.id}: адрес {check.url_id} не перенесён")
        tracked_link_id = links[check.url_id]
        task_id = db.execute(insert(check_tasks).values(
            run_id=run_ids[check.run_id], tracked_link_id=tracked_link_id, status="done",
            created_at=check.checked_at, finished_at=check.checked_at,
        ).returning(check_tasks.c.id)).scalar_one()
        observation_id = db.execute(insert(price_observations).values(
            tracked_link_id=tracked_link_id, task_id=task_id, checked_at=check.checked_at, status="ok",
            price_minor=found.price_minor, card_price_minor=found.card_price_minor, availability=found.availability,
            title=found.title, region_code=REGION_CODE, confirmation=found.confirmation, source=found.source,
        ).returning(price_observations.c.id)).scalar_one()
        if found.decision is not None:
            decisions.add(owner_id, observation_id, found.decision, now)
            decision_count += 1
    return run_ids, len(mapped), decision_count


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python tools/import_legacy.py", description="Перенос данных старого сайта в новую базу.")
    parser.add_argument("database", type=Path, help="файл старой базы pricewatch.db")
    parser.add_argument("--login", required=True, help="логин владельца данных на новом сайте")
    parser.add_argument("--replace", action="store_true", help="сначала удалить его категории, как кнопка «Удалить» на сайте")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(None)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        summary = import_legacy(engine, read_legacy(args.database), args.login, args.replace, datetime.now(UTC))
    except LegacyError as error:
        print(f"Перенос отменён, в новой базе ничего не изменилось: {error}", file=sys.stderr)
        return 1
    except PriceWatcherError as error:
        print(f"Перенос отменён, в новой базе ничего не изменилось: {error.code}: {error.safe_message}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(
        f"Перенесено: категорий {summary.categories}, групп {summary.groups}, товаров {summary.items}, магазинов {summary.stores}, "
        f"адресов ссылок {summary.links}, запусков {summary.runs}, проверок {summary.observations}, решений {summary.decisions}. "
        f"Удалено прежних категорий: {summary.replaced_categories}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
