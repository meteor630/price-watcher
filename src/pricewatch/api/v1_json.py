"""Общие части ответов /api/v1: изменение, точка графика, магазин, строка товара, покупка, время в UTC."""

from datetime import UTC, datetime
from typing import Any

from pricewatch.application.price_view import ItemRowView, StoreView
from pricewatch.application.purchases import PurchaseView
from pricewatch.domain.charts import ChartPoint
from pricewatch.domain.money import Change


def utc_iso(moment: datetime) -> str:
    """Дата-время для API — всегда UTC (openapi/v1.yaml, общие правила)."""
    return moment.astimezone(UTC).isoformat()


def change_json(change: Change | None) -> dict[str, Any] | None:
    if change is None:
        return None
    return {"direction": change.direction.value, "change_minor": change.change_minor, "change_text": change.change_text, "percent_text": change.percent_text}


def point_json(point: ChartPoint) -> dict[str, Any]:
    return {"date": point.day.isoformat(), "value_minor": point.value_minor, "label": point.label, "status": point.status.value}


def store_json(store: StoreView | None) -> dict[str, Any] | None:
    return None if store is None else {"id": store.store_id, "name": store.name, "color_token": store.color_token}


def purchase_json(view: PurchaseView | None) -> dict[str, Any] | None:
    if view is None:
        return None
    return {
        "item_id": view.item_id,
        "name": view.name,
        "purchased_at": utc_iso(view.purchased_at),
        "price_minor": view.price_minor,
        "price_text": view.price_text,
        "quantity": view.quantity,
        "total_minor": view.total_minor,
        "total_text": view.total_text,
        "store": store_json(view.store),
    }


def item_row_json(row: ItemRowView) -> dict[str, Any]:
    return {
        "id": row.item_id,
        "version": row.version,
        "name": row.name,
        "quantity": row.quantity,
        "in_total": row.in_total,
        "price_status": row.price_status.value,
        "under_review": row.under_review,
        "best_store": store_json(row.best_store),
        "unit_price_minor": row.unit_price_minor,
        "unit_price_text": row.unit_price_text,
        "line_price_minor": row.line_price_minor,
        "line_price_text": row.line_price_text,
        "regular_price_text": row.regular_price_text,
        "change": change_json(row.change),
        "checked_at": None if row.checked_at is None else utc_iso(row.checked_at),
    }
