"""«Требует решения» на настоящей базе: карточки пользователя, принять/отклонить с отменой, перепроверка, журнал, чужое — NOT_FOUND."""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from test_dashboard import DAY, NOW, World
from test_user_decisions import suspicious, watch

from pricewatch.application.reviews import RECHECKS_PER_HOUR, decide, recheck, review_cards
from pricewatch.application.shopping import undo_delete
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.review import Decision, ReviewKind
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"reviews-key-" + b"k" * 30


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("TRUNCATE users, stores, workers, settings CASCADE"))


def uow(engine: Engine) -> SqlUnitOfWork:
    return SqlUnitOfWork(engine)


def cards(engine: Engine, world: World, at: datetime = NOW):  # type: ignore[no-untyped-def]
    return review_cards(uow(engine), world.user, at)


def code(action) -> tuple[ErrorCode, object]:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    return caught.value.code, caught.value.details


def audit(world: World) -> list[str]:
    with world.engine.connect() as db:
        return [row[0] for row in db.execute(text("SELECT action FROM audit_events WHERE actor_id = :u ORDER BY id"), {"u": world.user.id})]


def shared_jump(engine: Engine) -> tuple[World, World, int, int, int]:
    me, other = World(engine), World(engine)
    store = me.store("Ozon")
    mine = me.item(me.category("Компьютер"), "Монитор")
    link = me.link(mine, store)
    watch(other, link, store)
    me.seen(link, NOW - 3 * DAY, 30_000_00)
    jump = suspicious(me, link, NOW - DAY, 45_000_00)
    return me, other, link, mine, jump


def test_cards_show_what_changed_for_each_user(engine: Engine) -> None:
    me, other, link, mine, jump = shared_jump(engine)

    [card] = cards(engine, me)

    assert (card.review_id, card.tracked_link_id, card.kinds, card.store.name) == (jump, link, {ReviewKind.PRICE_JUMP}, "Ozon")
    assert (card.before_price_text, card.after_price_text, [ref.item_id for ref in card.items]) == ("30 000\xa0₽", "45 000\xa0₽", [mine])
    assert len(cards(engine, other)) == 1 and me.board().reviews_pending == 1


def test_accept_reject_and_undo(engine: Engine) -> None:
    me, other, link, _, jump = shared_jump(engine)

    token = decide(uow(engine), KEY, me.user, jump, Decision.ACCEPT, NOW)
    decide(uow(engine), KEY, other.user, jump, Decision.REJECT, NOW)

    assert (cards(engine, me), cards(engine, other)) == ([], [])
    assert (me.board().total_minor, other.board().total_minor) == (45_000_00, 30_000_00)
    assert code(lambda: decide(uow(engine), KEY, me.user, jump, Decision.REJECT, NOW))[0] is ErrorCode.NOT_FOUND  # карточка уже закрыта

    undo_delete(uow(engine), KEY, me.user, token, NOW + timedelta(seconds=5))

    assert [card.review_id for card in cards(engine, me)] == [jump] and me.board().total_minor == 30_000_00
    assert audit(me) == ["price_decision", "price_decision_undone"]
    assert code(lambda: undo_delete(uow(engine), KEY, me.user, token, NOW))[0] is ErrorCode.NOT_FOUND


def test_only_latest_open_review_of_own_link(engine: Engine) -> None:
    me, _, link, _, jump = shared_jump(engine)
    stranger = World(engine)
    newer = suspicious(me, link, NOW - timedelta(hours=2), 46_000_00)

    assert [card.review_id for card in cards(engine, me)] == [newer]
    assert code(lambda: decide(uow(engine), KEY, me.user, jump, Decision.ACCEPT, NOW))[0] is ErrorCode.NOT_FOUND
    assert code(lambda: decide(uow(engine), KEY, stranger.user, newer, Decision.ACCEPT, NOW))[0] is ErrorCode.NOT_FOUND
    assert code(lambda: decide(uow(engine), KEY, me.user, 10**12, Decision.ACCEPT, NOW))[0] is ErrorCode.NOT_FOUND

    decide(uow(engine), KEY, me.user, newer, Decision.ACCEPT, NOW)  # решение по последнему закрывает и более ранний

    assert cards(engine, me) == [] and me.board().total_minor == 46_000_00


def test_title_change_card_and_no_recheck(engine: Engine) -> None:
    me = World(engine)
    store = me.store()
    link = me.link(me.item(me.category("Дом"), "Монитор"), store)
    me.one("UPDATE tracked_links SET baseline_title = 'Монитор LG 27GR93U' WHERE id = :l RETURNING id", l=link)
    me.seen(link, NOW - 3 * DAY, 30_000_00)
    renamed = suspicious(me, link, NOW - DAY, 30_000_00, title="Холодильник Атлант")

    [card] = cards(engine, me)

    assert (card.kinds, card.before_title, card.after_title) == ({ReviewKind.TITLE_CHANGED}, "Монитор LG 27GR93U", "Холодильник Атлант")
    assert code(lambda: recheck(uow(engine), KEY, me.user, renamed, NOW)) == (
        ErrorCode.VALIDATION_ERROR, {"fields": {"action": "recheck_needs_price_jump"}}
    )


def test_recheck_queues_once_and_undo_cancels_until_taken(engine: Engine) -> None:
    me, other, link, _, jump = shared_jump(engine)

    first = recheck(uow(engine), KEY, me.user, jump, NOW)
    second = recheck(uow(engine), KEY, other.user, jump, NOW)

    tasks = me.one("SELECT count(*) FROM check_tasks WHERE tracked_link_id = :l AND status = 'queued'", l=link)
    assert first.undo_token is not None and second.undo_token is None and tasks == 1
    undo_delete(uow(engine), KEY, me.user, first.undo_token, NOW)
    assert me.one("SELECT count(*) FROM check_tasks WHERE tracked_link_id = :l AND status = 'queued'", l=link) == 0
    assert me.one("SELECT status FROM parse_runs WHERE kind = 'on_add' ORDER BY id DESC LIMIT 1") == "finished"

    again = recheck(uow(engine), KEY, me.user, jump, NOW)
    worker = me.one("INSERT INTO workers (name, token_hash) VALUES ('w', md5('w') || md5('w')) RETURNING id")
    me.one("UPDATE check_tasks SET status = 'leased', worker_id = :w, lease_until = :t, attempt = 1 WHERE tracked_link_id = :l AND status = 'queued' RETURNING id",
           w=worker, t=NOW + timedelta(minutes=2), l=link)
    assert code(lambda: undo_delete(uow(engine), KEY, me.user, again.undo_token, NOW)) == (ErrorCode.NOT_FOUND, {"undo": "started"})
    assert audit(me) == ["price_recheck", "price_recheck_undone", "price_recheck"]


def test_recheck_limit_per_hour(engine: Engine) -> None:
    me, _, _, _, jump = shared_jump(engine)
    for minutes in range(RECHECKS_PER_HOUR):
        me.one("INSERT INTO audit_events (actor_id, action, entity_type, created_at) VALUES (:u, 'price_recheck', 'tracked_link', :t) RETURNING id",
               u=me.user.id, t=NOW - timedelta(minutes=59 - minutes))

    error = code(lambda: recheck(uow(engine), KEY, me.user, jump, NOW))

    assert error == (ErrorCode.RATE_LIMITED, {"retry_after_seconds": 60})
    # Ровно через обещанные 60 секунд самая ранняя перепроверка вышла из часового окна.
    assert recheck(uow(engine), KEY, me.user, jump, NOW + timedelta(seconds=60)).undo_token is not None


def test_purchased_items_have_no_cards(engine: Engine) -> None:
    me = World(engine)
    store = me.store()
    item = me.item(me.category("Дом"), "Куплено", state="purchased")
    link = me.link(item, store)
    me.seen(link, NOW - 3 * DAY, 1000_00)
    suspicious(me, link, NOW - DAY, 3000_00)

    assert cards(engine, me) == [] and me.board().reviews_pending == 0
