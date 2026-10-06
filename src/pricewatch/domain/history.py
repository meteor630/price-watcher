"""История изменений ссылки для карточки товара (SITE_DESIGN_SPEC.md, раздел 6; схема HistoryEvent).

Событие — перемена относительно предыдущего подтверждённого состояния магазина:
- цена сменилась: «12 990 ₽ → 11 490 ₽» (действующая цена — по карте, если есть);
- товар закончился и снова появился;
- проверка не удалась после успешной (повторные сбои подряд — одно событие).
Неподтверждённые и отклонённые наблюдения событий не дают: на проверке цена не меняется.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pricewatch.domain.errors import SAFE_MESSAGES
from pricewatch.domain.money import format_rub
from pricewatch.domain.pricing import Availability, Confirmation, Observation


class EventKind(StrEnum):
    PRICE_CHANGED = "price_changed"
    OUT_OF_STOCK = "out_of_stock"
    BACK_IN_STOCK = "back_in_stock"
    CHECK_FAILED = "check_failed"


@dataclass(frozen=True, slots=True)
class HistoryEvent:
    at: datetime
    kind: EventKind
    text: str


def link_history(observations: Sequence[Observation]) -> list[HistoryEvent]:
    """События одной ссылки по возрастанию времени."""
    events: list[HistoryEvent] = []
    price: int | None = None
    in_stock: bool | None = None
    failing = False
    for observation in sorted(observations, key=lambda item: item.checked_at):
        if observation.failure is not None:
            if not failing and in_stock is not None:
                events.append(HistoryEvent(observation.checked_at, EventKind.CHECK_FAILED, f"Не удалось проверить: {SAFE_MESSAGES[observation.failure]}"))
            failing = True
            continue
        if observation.confirmation is not Confirmation.CONFIRMED:
            continue
        failing = False
        available = observation.availability is not Availability.OUT_OF_STOCK
        if in_stock is not None and available != in_stock:
            kind = EventKind.BACK_IN_STOCK if available else EventKind.OUT_OF_STOCK
            events.append(HistoryEvent(observation.checked_at, kind, "Снова в наличии" if available else "Нет в наличии"))
        current = observation.effective_price_minor
        if available and current is not None:
            if price is not None and current != price:
                events.append(HistoryEvent(observation.checked_at, EventKind.PRICE_CHANGED, f"{format_rub(price)} → {format_rub(current)}"))
            price = current
        in_stock = available
    return events
