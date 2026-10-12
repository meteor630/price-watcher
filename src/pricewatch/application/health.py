"""Проверка здоровья для Docker и сторожа: база отвечает на запрос в транзакции.

Недоступная база — PriceWatcherError(DB_ERROR) из единицы работы; наружу уходит только код ошибки.
"""

from pricewatch.application.ports import UnitOfWork

PROBE_SETTING = "stale_after_days"


def check_health(uow: UnitOfWork) -> None:
    with uow:
        # Любой настоящий запрос: открытого соединения из пула мало, оно могло умереть.
        uow.settings.get(PROBE_SETTING)
