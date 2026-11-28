"""«Требует решения»: подозрительные изменения по товарам пользователя (DEVELOPMENT_ARCHITECTURE.md, раздел 6).

Каждый решает только за свои товары (решение владельца 07.10.2026). Карточка — одна на ссылку,
привязанную сейчас к товару «в списке»: последнее наблюдение, ждущее решения пользователя
(domain.review.open_review), с тем, что изменилось относительно его последней засчитанной цены и эталона.

- «Принять» и «Отклонить» записывают его решение; «Отменить» (15 секунд) удаляет его.
- «Перепроверить сейчас» — только при скачке цены: срочное задание на одну ссылку; лимит — 20 в час
  на пользователя; уже идёт проверка ссылки — новое задание не ставится. «Отменить» снимает задание,
  пока worker его не взял.
- Каждое действие пишется в журнал (audit_events).
"""

from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.manual_checks import ensure_manual_check_allowed, queue_manual_checks
from pricewatch.application.ports import AuditEvent, LinkBinding, UnitOfWork
from pricewatch.application.price_view import StoreView, review_thresholds, user_contexts
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.money import Change, format_rub, price_change
from pricewatch.domain.review import Decision, OpenReview, ReviewKind, open_review
from pricewatch.domain.shopping import ItemState
from pricewatch.domain.undo import UndoKind, issue_undo



@dataclass(frozen=True, slots=True)
class ItemRef:
    item_id: int
    name: str


@dataclass(frozen=True, slots=True)
class ReviewCard:
    review_id: int
    tracked_link_id: int
    kinds: frozenset[ReviewKind]
    store: StoreView
    url: str
    items: list[ItemRef]
    checked_at: datetime
    before_price_text: str | None
    after_price_text: str | None
    before_title: str | None
    after_title: str | None
    # Изменение цены за штуку (у скачка цены), со знаком и процентом.
    change: Change | None


@dataclass(frozen=True, slots=True)
class Recheck:
    """Итог «Перепроверить»: токен «Отменить»; None — проверка ссылки уже идёт, задание не ставилось."""

    undo_token: str | None


def review_cards(uow: UnitOfWork, user: User, now: datetime) -> list[ReviewCard]:
    """Карточки пользователя, сначала давние."""
    with uow:
        return cards_in(uow, user, now)


def cards_in(uow: UnitOfWork, user: User, now: datetime) -> list[ReviewCard]:
    """То же внутри уже открытой транзакции (главная считает их число)."""
    watched = _watched(uow, user, now)
    link_ids = sorted(watched)
    if not link_ids:
        return []
    checked = uow.price_data.observations(link_ids, now)
    contexts = user_contexts(uow, user.id, link_ids, uow.price_data.bindings(user.id))
    thresholds = review_thresholds(uow)
    found = {link_id: review for link_id in link_ids if (review := open_review(checked[link_id], contexts[link_id], thresholds)) is not None}
    if not found:
        return []
    stores = uow.price_data.stores(sorted({watched[link_id][0].store_id for link_id in found}))
    links = uow.price_data.links(sorted(found))
    names = {item.item_id: item.name for item in uow.price_data.items(user.id)}
    cards = []
    for link_id, review in found.items():
        store = stores[watched[link_id][0].store_id]
        items = sorted((ItemRef(binding.item_id, names[binding.item_id]) for binding in watched[link_id]), key=lambda ref: (ref.name, ref.item_id))
        cards.append(_card(review, link_id, StoreView(store.store_id, store.name, store.color_token), links[link_id].url, items))
    cards.sort(key=lambda card: (card.checked_at, card.review_id))
    return cards


def decide(uow: UnitOfWork, key: bytes, user: User, review_id: int, decision: Decision, now: datetime) -> str:
    """Решение по открытой карточке пользователя; токен «Отменить». Чужая или закрытая карточка — NOT_FOUND."""
    with uow:
        card = _open_card(uow, user, review_id, now)
        decision_id = uow.decisions.add(user.id, review_id, decision, now)
        uow.audit.add(AuditEvent(user.id, "price_decision", "price_observation", review_id, None,
                                 {"decision": decision.value, "kinds": sorted(card.kinds), "tracked_link_id": card.tracked_link_id}, now))
        uow.commit()
    return issue_undo(key, user.id, UndoKind.DECISION, decision_id, now)


def recheck(uow: UnitOfWork, key: bytes, user: User, review_id: int, now: datetime) -> Recheck:
    with uow:
        card = _open_card(uow, user, review_id, now)
        if ReviewKind.PRICE_JUMP not in card.kinds:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"action": "recheck_needs_price_jump"}})
        ensure_manual_check_allowed(uow, user.id, now)
        queued = queue_manual_checks(uow, user.id, [card.tracked_link_id], ("tracked_link", card.tracked_link_id), {"review_id": review_id}, now)
        uow.commit()
    if not queued.new_task_ids:
        return Recheck(None)
    return Recheck(issue_undo(key, user.id, UndoKind.RECHECK, queued.new_task_ids[0], now))


def undo_decision(uow: UnitOfWork, user: User, decision_id: int, now: datetime) -> None:
    """Внутри открытой транзакции «Отменить»: своё решение удаляется; чужого или уже удалённого нет — NOT_FOUND."""
    removed = uow.decisions.delete(user.id, decision_id)
    if removed is None:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    observation_id, decision = removed
    uow.audit.add(AuditEvent(user.id, "price_decision_undone", "price_observation", observation_id, {"decision": decision.value}, None, now))


def undo_recheck(uow: UnitOfWork, user: User, task_id: int, now: datetime) -> None:
    """Внутри открытой транзакции «Отменить»: задание снимается, пока worker его не взял."""
    if not uow.queue.cancel_queued(task_id, now):
        raise PriceWatcherError(ErrorCode.NOT_FOUND, {"undo": "started"})
    uow.audit.add(AuditEvent(user.id, "price_recheck_undone", "check_task", task_id, None, None, now))


def _watched(uow: UnitOfWork, user: User, now: datetime) -> dict[int, list[LinkBinding]]:
    """Ссылки, привязанные сейчас к товарам пользователя «в списке», и эти привязки."""
    active = {item.item_id for item in uow.price_data.items(user.id) if item.state is ItemState.ACTIVE}
    watched: dict[int, list[LinkBinding]] = {}
    for binding in uow.price_data.bindings(user.id):
        if binding.item_id in active and binding.valid_from <= now and (binding.valid_to is None or binding.valid_to > now):
            watched.setdefault(binding.tracked_link_id, []).append(binding)
    return watched


def _open_card(uow: UnitOfWork, user: User, review_id: int, now: datetime) -> ReviewCard:
    link_id = uow.observations.link_of(review_id)
    card = next((card for card in cards_in(uow, user, now) if card.tracked_link_id == link_id), None) if link_id is not None else None
    if card is None or card.review_id != review_id:
        # Чужая ссылка, уже решённое или устаревшее наблюдение — для пользователя такой карточки нет.
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return card


def _card(review: OpenReview, link_id: int, store: StoreView, url: str, items: list[ItemRef]) -> ReviewCard:
    def text(price: int | None) -> str | None:
        return None if price is None else format_rub(price)

    return ReviewCard(
        review.observation_id, link_id, review.kinds, store, url, items, review.checked_at,
        text(review.before_price_minor), text(review.after_price_minor), review.before_title, review.after_title,
        None if review.before_price_minor is None or review.after_price_minor is None else price_change(review.before_price_minor, review.after_price_minor),
    )
