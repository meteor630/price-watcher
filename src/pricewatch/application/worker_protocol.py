"""Протокол worker (DEVELOPMENT_ARCHITECTURE.md, раздел 8; утверждён владельцем 06.10.2026).

- Токен worker — 32 случайных байта, в базе HMAC с назначением WORKER. Ротация: администратор
  выпускает новый токен, старый и новый действуют вместе; первый вход с новым гасит старый.
- Задание выдаётся в аренду на 2 минуты, worker продлевает её, пока работает. Одновременно —
  не больше одного задания на магазин; магазины выключенные и на паузе пропускаются.
- Результат — недоверенный ввод: допустимые статусы, цены в копейках в пределах, длины строк.
  Результат по чужому или просроченному заданию отклоняется (NOT_FOUND); повтор уже принятого
  результата ничего не меняет.
- Успешная проверка проходит правила подозрительных изменений (раздел 6): смена названия или скачок
  цены ждут решения администратора; ценовое подозрение закрывается само, если новая цена вернулась
  в порог. Первое название ссылки становится эталонным.
- Блокировка (403/429) или капча ставят магазин на паузу по ступеням (domain.store_pause). Пока ступени
  не исчерпаны, задание ждёт в очереди, в историю ничего не пишется. Исчерпаны — магазин отдыхает до
  следующего планового запуска, задание и все ждущие задания магазина закрываются этой ошибкой,
  администраторы получают уведомление. Любой другой ответ магазина снимает паузу.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pricewatch.application.ports import ClaimedTask, LeasedTask, NewObservation, UnitOfWork
from pricewatch.application.runs import configured_times
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.names import clean_name
from pricewatch.domain.pricing import Availability, Confirmation
from pricewatch.domain.review import ReviewKind, Thresholds, closes_price_review, review
from pricewatch.domain.schedule import next_slot_at
from pricewatch.domain.store_pause import PAUSE_REASONS, next_step
from pricewatch.domain.tokens import Purpose, keyed_hash, new_token

LEASE = timedelta(minutes=2)
WORKER_NAME_MAX = 80
TITLE_MAX = 500
SITE_NAME_MAX = 100
DURATION_MAX_MS = 10 * 60 * 1000
# 10 млрд ₽ — как предел цены покупки: заведомо помещается в bigint.
PRICE_MAX = 10**12
FETCH_METHODS = frozenset({"http", "browser"})
SOURCES = frozenset({"site_data", "page"})
# Ошибки сбора цен, которые может прислать worker (как в таблицах наблюдений и заданий).
FETCH_ERRORS = frozenset(
    {
        ErrorCode.INVALID_URL, ErrorCode.STORE_NOT_ALLOWED, ErrorCode.BLOCKED, ErrorCode.CAPTCHA, ErrorCode.TIMEOUT,
        ErrorCode.LAYOUT_CHANGED, ErrorCode.PRICE_NOT_FOUND, ErrorCode.REGION_MISMATCH, ErrorCode.UNSUPPORTED_CURRENCY,
        ErrorCode.INTERNAL_ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class WorkerResult:
    """Что прислал worker; `status` — "ok" или код ошибки сбора цен."""

    status: str
    price_minor: int | None = None
    card_price_minor: int | None = None
    availability: str = "unknown"
    title: str | None = None
    source: str | None = None
    site_name: str | None = None
    fetch_method: str | None = None
    duration_ms: int | None = None


def create_worker(uow: UnitOfWork, key: bytes, name: str, now: datetime) -> str:
    """Новый worker; токен показывается один раз, в базе остаётся только HMAC."""
    clean = clean_name(name, field="name", max_length=WORKER_NAME_MAX)
    token = new_token()
    with uow:
        uow.workers.create(clean, keyed_hash(key, Purpose.WORKER, token), now)
        uow.commit()
    return token


def rotate_worker_token(uow: UnitOfWork, key: bytes, name: str) -> str:
    """Новый токен worker; старый действует, пока worker не войдёт с новым."""
    token = new_token()
    with uow:
        uow.workers.set_next_token(clean_name(name, field="name", max_length=WORKER_NAME_MAX), keyed_hash(key, Purpose.WORKER, token))
        uow.commit()
    return token


def authenticate_worker(uow: UnitOfWork, key: bytes, token: str | None, now: datetime) -> int:
    if not token:
        raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
    with uow:
        auth = uow.workers.find_by_token(keyed_hash(key, Purpose.WORKER, token))
        if auth is None or auth.disabled:
            raise PriceWatcherError(ErrorCode.AUTH_REQUIRED)
        if auth.used_next_token:
            uow.workers.promote_next_token(auth.worker_id)
        uow.workers.touch(auth.worker_id, now)
        uow.commit()
    return auth.worker_id


def claim_task(uow: UnitOfWork, worker_id: int, now: datetime) -> ClaimedTask | None:
    with uow:
        task = uow.queue.claim(worker_id, now, now + LEASE)
        uow.commit()
    return task


def extend_lease(uow: UnitOfWork, worker_id: int, task_id: int, now: datetime) -> datetime:
    lease_until = now + LEASE
    with uow:
        if not uow.queue.extend(worker_id, task_id, now, lease_until):
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        uow.commit()
    return lease_until


def submit_result(
    uow: UnitOfWork, worker_id: int, task_id: int, result: WorkerResult, now: datetime, thresholds: Thresholds = Thresholds()
) -> None:
    checked = _validated(result)
    with uow:
        if uow.observations.exists_for_task(task_id):
            return  # повтор уже принятого результата — ничего не меняем
        task = uow.queue.leased(worker_id, task_id, now)
        if task is None:
            raise PriceWatcherError(ErrorCode.NOT_FOUND)
        exhausted = False
        if checked.status in {reason.value for reason in PAUSE_REASONS}:
            exhausted = _pause_store(uow, task, ErrorCode(checked.status), now)
            if not exhausted:
                uow.queue.wait_paused(task)
                uow.commit()
                return
        else:
            uow.stores.clear_pause(task.store_id)
        confirmation = Confirmation.CONFIRMED
        if checked.status == "ok":
            confirmation = _review(uow, task.tracked_link_id, checked, thresholds)
            uow.stores.learn(task.store_id, checked.fetch_method, checked.site_name)
        uow.observations.add(
            NewObservation(
                task.tracked_link_id, task.task_id, now, checked.status, checked.price_minor, checked.card_price_minor,
                checked.availability, checked.title, task.region_code, confirmation.value, checked.source,
            )
        )
        if exhausted:
            uow.queue.finish_exhausted(task, checked.status, checked.duration_ms, now)
        else:
            failed = checked.status != "ok"
            uow.queue.finish(task, failed, checked.status if failed else None, checked.duration_ms, now)
        uow.commit()


def _pause_store(uow: UnitOfWork, task: LeasedTask, reason: ErrorCode, now: datetime) -> bool:
    """Поставить магазин на следующую ступень паузы; True — ступени исчерпаны."""
    state = uow.stores.pause_state(task.store_id)
    step = next_step(reason, state.reason, state.level)
    if step.delay is not None:
        uow.stores.pause(task.store_id, reason.value, step.level, now + step.delay)
        return False
    until = next_slot_at(configured_times(uow), now)
    uow.stores.pause(task.store_id, reason.value, step.level, until)
    uow.notifications.notify_admins(
        "store_paused",
        f"store_paused:{task.store_id}:{until.isoformat()}",
        {"store": state.display_name, "host": state.canonical_host, "reason": reason.value, "paused_until": until.isoformat()},
        now,
    )
    return True


def _review(uow: UnitOfWork, tracked_link_id: int, result: WorkerResult, thresholds: Thresholds) -> Confirmation:
    baseline = uow.tracked_links.baseline_title(tracked_link_id)
    if baseline is None and result.title is not None:
        uow.tracked_links.set_baseline_title(tracked_link_id, result.title)
        baseline = result.title
    last_price = uow.observations.last_confirmed_price(tracked_link_id)
    price = _effective(result)
    verdict = review(baseline, last_price, result.title, price, thresholds)
    if last_price is not None and closes_price_review(last_price, price, thresholds):
        # Ценовое подозрение, у которого нет подозрения на название, закрывается: цена вернулась в порог.
        stale = [
            pending.id
            for pending in uow.observations.pending(tracked_link_id)
            if review(baseline, last_price, pending.title, pending.effective_price_minor, thresholds).kinds == {ReviewKind.PRICE_JUMP}
        ]
        if stale:
            uow.observations.reject(stale)
    return verdict.confirmation


def _effective(result: WorkerResult) -> int | None:
    if result.availability == Availability.OUT_OF_STOCK.value:
        return None
    return result.card_price_minor if result.card_price_minor is not None else result.price_minor


def _validated(result: WorkerResult) -> WorkerResult:
    """Недоверенный ввод worker: всё в пределах, иначе VALIDATION_ERROR с полем."""
    if result.status != "ok" and result.status not in {code.value for code in FETCH_ERRORS}:
        raise _invalid("status")
    if result.availability not in {value.value for value in Availability}:
        raise _invalid("availability")
    if result.source is not None and result.source not in SOURCES:
        raise _invalid("source")
    if result.fetch_method is not None and result.fetch_method not in FETCH_METHODS:
        raise _invalid("fetch_method")
    if result.duration_ms is not None and (isinstance(result.duration_ms, bool) or not 0 <= result.duration_ms <= DURATION_MAX_MS):
        raise _invalid("duration_ms")
    title = _text(result.title, "title", TITLE_MAX)
    site_name = _text(result.site_name, "site_name", SITE_NAME_MAX)
    for field, value in (("price_minor", result.price_minor), ("card_price_minor", result.card_price_minor)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= PRICE_MAX):
            raise _invalid(field)
    if result.status == "ok":
        # Как в domain.pricing.Observation: товар не «нет в наличии» — обычная цена обязательна.
        if result.availability != Availability.OUT_OF_STOCK.value and result.price_minor is None:
            raise _invalid("price_minor")
        if result.source is None:
            raise _invalid("source")
    elif result.price_minor is not None or result.card_price_minor is not None or result.source is not None:
        raise _invalid("price_minor")  # сбой не несёт цены
    return WorkerResult(
        result.status, result.price_minor, result.card_price_minor, result.availability, title, result.source,
        site_name, result.fetch_method, result.duration_ms,
    )


def _text(value: str | None, field: str, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > limit or any(ord(char) < 32 for char in text):
        raise _invalid(field)
    return text


def _invalid(field: str) -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {field: "invalid"}})
