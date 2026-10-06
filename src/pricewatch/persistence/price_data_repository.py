"""Чтение данных для расчёта цен на страницах. Правила — в pricewatch.application.ports (PriceDataRepository).

Наблюдения берутся не за всю историю: для расчёта на любой момент после `since` достаточно
всего, что после `since`, и того, что начиная с последнего успешного подтверждённого до него.
Так страница за месяц не читает годовую историю всех ссылок.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, Table, func, select

from pricewatch.application.ports import LinkBinding, PricedItem, StoreInfo
from pricewatch.domain.errors import ErrorCode
from pricewatch.domain.pricing import Availability, Confirmation, Observation
from pricewatch.domain.shopping import ItemState
from pricewatch.persistence.models import CategoryRow, GroupRow, ItemLinkRow, ItemRow, PriceObservationRow, StoreRow

items: Table = ItemRow.__table__
categories: Table = CategoryRow.__table__
groups: Table = GroupRow.__table__
item_links: Table = ItemLinkRow.__table__
stores: Table = StoreRow.__table__
observations: Table = PriceObservationRow.__table__


class SqlPriceDataRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def items(self, user_id: int) -> list[PricedItem]:
        rows = self.connection.execute(
            select(items.c.id, items.c.category_id, items.c.name, items.c.quantity, items.c.in_total, items.c.include_stale,
                   items.c.state, items.c.purchased_at)
            .select_from(items.join(categories, categories.c.id == items.c.category_id).outerjoin(groups, groups.c.id == items.c.group_id))
            .where(
                items.c.user_id == user_id,
                items.c.deleted_at.is_(None),
                categories.c.deleted_at.is_(None),
                items.c.group_id.is_(None) | groups.c.deleted_at.is_(None),
            )
            .order_by(categories.c.position, categories.c.id, items.c.position, items.c.id)
        )
        return [
            PricedItem(row.id, row.category_id, row.name, row.quantity, row.in_total, row.include_stale, ItemState(row.state), row.purchased_at)
            for row in rows
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

    def observations(self, tracked_link_ids: Sequence[int], since: datetime) -> dict[int, list[Observation]]:
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
        found: dict[int, list[Observation]] = {link_id: [] for link_id in ids}
        for row in rows:
            found[row.tracked_link_id].append(
                Observation(
                    checked_at=row.checked_at,
                    failure=None if row.status == "ok" else ErrorCode(row.status),
                    price_minor=row.price_minor,
                    card_price_minor=row.card_price_minor,
                    availability=Availability(row.availability),
                    confirmation=Confirmation(row.confirmation),
                )
            )
        return found

    def pending_reviews(self) -> int:
        return self.connection.execute(select(func.count()).where(observations.c.confirmation == "pending")).scalar_one()
