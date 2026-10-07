"""Чтение данных для расчёта цен на страницах. Правила — в pricewatch.application.ports (PriceDataRepository).

Наблюдения берутся не за всю историю: для расчёта на любой момент после `since` достаточно
всего, что после `since`, и того, что начиная с последнего успешного подтверждённого до него.
Так страница за месяц не читает годовую историю всех ссылок.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, Connection, Join, Table, func, select, true

from pricewatch.application.ports import LinkBinding, LinkInfo, LinkState, PricedItem, PurchasedItem, ReviewState, StoreInfo
from pricewatch.domain.errors import ErrorCode
from pricewatch.domain.pricing import Availability, Confirmation, Observation
from pricewatch.domain.review import Checked, Decision
from pricewatch.domain.shopping import ItemState, Purchase
from pricewatch.persistence.models import (
    CategoryRow, CheckTaskRow, GroupRow, ItemLinkRow, ItemRow, PriceDecisionRow, PriceObservationRow, StoreRow, TrackedLinkRow,
)

items: Table = ItemRow.__table__
categories: Table = CategoryRow.__table__
groups: Table = GroupRow.__table__
item_links: Table = ItemLinkRow.__table__
stores: Table = StoreRow.__table__
observations: Table = PriceObservationRow.__table__
tracked_links: Table = TrackedLinkRow.__table__
check_tasks: Table = CheckTaskRow.__table__
decisions: Table = PriceDecisionRow.__table__



def _visible() -> Join:
    """Товар виден на страницах вместе со своей категорией и группой: удалённая группа скрывает и свои товары."""
    return items.join(categories, categories.c.id == items.c.category_id).outerjoin(groups, groups.c.id == items.c.group_id)


def _visible_to(user_id: int) -> tuple[ColumnElement[bool], ...]:
    return (
        items.c.user_id == user_id,
        items.c.deleted_at.is_(None),
        categories.c.deleted_at.is_(None),
        items.c.group_id.is_(None) | groups.c.deleted_at.is_(None),
    )


class SqlPriceDataRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def items(self, user_id: int) -> list[PricedItem]:
        rows = self.connection.execute(
            select(items.c.id, items.c.category_id, items.c.group_id, items.c.version, items.c.name, items.c.quantity, items.c.in_total,
                   items.c.include_stale, items.c.state, items.c.purchased_at, items.c.archived_at)
            .select_from(_visible())
            .where(*_visible_to(user_id))
            .order_by(categories.c.position, categories.c.id, groups.c.position.nulls_first(), items.c.group_id.nulls_first(), items.c.position, items.c.id)
        )
        return [
            PricedItem(
                row.id, row.category_id, row.group_id, row.version, row.name, row.quantity, row.in_total, row.include_stale,
                ItemState(row.state), row.purchased_at, row.archived_at,
            )
            for row in rows
        ]

    def purchases(self, user_id: int, category_id: int | None = None) -> list[PurchasedItem]:
        query = (
            select(items.c.id, items.c.category_id, categories.c.name.label("category_name"), items.c.name, items.c.purchased_at,
                   items.c.purchase_price_minor, items.c.purchase_quantity, items.c.purchase_store_id, items.c.purchase_baseline_minor)
            .select_from(_visible())
            .where(*_visible_to(user_id), items.c.state == ItemState.PURCHASED.value)
            .order_by(items.c.purchased_at.desc(), items.c.id.desc())
        )
        if category_id is not None:
            query = query.where(items.c.category_id == category_id)
        return [
            PurchasedItem(
                row.id, row.category_id, row.category_name, row.name,
                Purchase(row.purchased_at, row.purchase_price_minor, row.purchase_quantity, row.purchase_store_id), row.purchase_baseline_minor,
            )
            for row in self.connection.execute(query)
        ]

    def bindings(self, user_id: int) -> list[LinkBinding]:
        rows = self.connection.execute(
            select(item_links.c.item_id, item_links.c.tracked_link_id, item_links.c.store_id, item_links.c.valid_from, item_links.c.valid_to)
            .where(item_links.c.user_id == user_id)
            .order_by(item_links.c.item_id, item_links.c.valid_from, item_links.c.id)
        )
        return [LinkBinding(row.item_id, row.tracked_link_id, row.store_id, row.valid_from, row.valid_to) for row in rows]

    def stores(self, store_ids: Sequence[int]) -> dict[int, StoreInfo]:
        if not store_ids:
            return {}
        rows = self.connection.execute(
            select(stores.c.id, stores.c.display_name, stores.c.color, stores.c.enabled).where(stores.c.id.in_(list(store_ids)))
        )
        return {row.id: StoreInfo(row.id, row.display_name, row.color, row.enabled) for row in rows}

    def links(self, tracked_link_ids: Sequence[int]) -> dict[int, LinkInfo]:
        if not tracked_link_ids:
            return {}
        rows = self.connection.execute(
            select(tracked_links.c.id, tracked_links.c.normalized_url, tracked_links.c.image_path).where(tracked_links.c.id.in_(list(tracked_link_ids)))
        )
        return {row.id: LinkInfo(row.normalized_url, row.image_path) for row in rows}

    def observations(self, tracked_link_ids: Sequence[int], since: datetime) -> dict[int, list[Checked]]:
        if not tracked_link_ids:
            return {}
        ids = list(tracked_link_ids)
        success = (observations.c.status == "ok") & (observations.c.confirmation == "confirmed")
        before = (
            select(
                observations.c.tracked_link_id,
                func.max(observations.c.checked_at).filter(success).label("success_before"),
                func.max(observations.c.checked_at).label("any_before"),
            )
            .where(observations.c.tracked_link_id.in_(ids), observations.c.checked_at < since)
            .group_by(observations.c.tracked_link_id)
            .cte("before_since")
        )
        start = func.coalesce(before.c.success_before, before.c.any_before, since)
        rows = self.connection.execute(
            select(observations)
            .outerjoin(before, before.c.tracked_link_id == observations.c.tracked_link_id)
            .where(observations.c.tracked_link_id.in_(ids), observations.c.checked_at >= start)
            .order_by(observations.c.tracked_link_id, observations.c.checked_at, observations.c.id)
        )
        found: dict[int, list[Checked]] = {link_id: [] for link_id in ids}
        for row in rows:
            observation = Observation(
                checked_at=row.checked_at,
                failure=None if row.status == "ok" else ErrorCode(row.status),
                price_minor=row.price_minor,
                card_price_minor=row.card_price_minor,
                availability=Availability(row.availability),
                confirmation=Confirmation(row.confirmation),
            )
            found[row.tracked_link_id].append(Checked(row.id, row.title, observation))
        return found

    def review_states(self, user_id: int, tracked_link_ids: Sequence[int]) -> dict[int, ReviewState]:
        ids = list(tracked_link_ids)
        if not ids:
            return {}
        decided = (
            select(observations.c.tracked_link_id, observations.c.id, observations.c.title, observations.c.checked_at, decisions.c.decision)
            .select_from(decisions.join(observations, observations.c.id == decisions.c.observation_id))
            .where(decisions.c.user_id == user_id, observations.c.tracked_link_id.in_(ids))
            .order_by(observations.c.tracked_link_id, observations.c.checked_at, observations.c.id)
        )
        rows = self.connection.execute(select(tracked_links.c.id, tracked_links.c.baseline_title).where(tracked_links.c.id.in_(ids)))
        baselines = {row.id: row.baseline_title for row in rows}
        states = {link_id: ReviewState(baselines.get(link_id), {}) for link_id in ids}
        for row in self.connection.execute(decided):
            state = states[row.tracked_link_id]
            state.decisions[row.id] = Decision(row.decision)
            if row.decision == Decision.ACCEPT.value and row.title is not None:
                # По времени проверки: последнее принятое название — эталон пользователя.
                states[row.tracked_link_id] = ReviewState(row.title, state.decisions)
        return states

    def link_states(self, user_id: int, item_id: int) -> list[LinkState]:
        last = (
            select(observations)
            .where(observations.c.tracked_link_id == item_links.c.tracked_link_id)
            .order_by(observations.c.checked_at.desc(), observations.c.id.desc())
            .limit(1)
            .lateral("last")
        )
        checking = (
            select(check_tasks.c.id)
            .where(check_tasks.c.tracked_link_id == item_links.c.tracked_link_id, check_tasks.c.status.in_(("queued", "leased")))
            .exists()
        )
        rows = self.connection.execute(
            select(item_links.c.id, tracked_links.c.normalized_url, stores.c.display_name, stores.c.color, stores.c.enabled, checking.label("checking"),
                   last.c.status, last.c.price_minor, last.c.card_price_minor, last.c.availability, last.c.source, last.c.checked_at)
            .select_from(
                item_links.join(tracked_links, tracked_links.c.id == item_links.c.tracked_link_id)
                .join(stores, stores.c.id == item_links.c.store_id)
                .outerjoin(last, true())
            )
            .where(item_links.c.user_id == user_id, item_links.c.item_id == item_id, item_links.c.valid_to.is_(None))
            .order_by(item_links.c.valid_from, item_links.c.id)
        )
        return [LinkState(*row) for row in rows]

