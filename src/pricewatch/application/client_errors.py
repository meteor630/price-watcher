"""Приём отчётов об ошибках приложения (DEVELOPMENT_ARCHITECTURE.md, раздел 10, «Отчёты об ошибках приложения»).

- Только от вошедшего устройства; лимит — 20 отчётов в час на устройство (сверх — RATE_LIMITED).
- Текст — недоверенный ввод: перед хранением вырезаются секреты (domain.client_errors.clean_text).
- Администраторы получают уведомление; одинаковые ошибки одной версии склеиваются (счётчик повторов).
- Отчёты хранятся 90 суток, старые стирает планировщик.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.ports import ClientErrorReport, UnitOfWork
from pricewatch.domain.client_errors import FUTURE_SKEW, KEPT_FOR, REPORT_WINDOW, REPORTS_PER_HOUR, clean_text
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

NOTIFICATION_MESSAGE_MAX = 300


@dataclass(frozen=True, slots=True)
class ClientReport:
    """Тело отчёта как прислало приложение (длины уже проверены на границе API)."""

    app_version: str
    os_version: str
    screen: str
    error_code: str
    message: str
    occurred_at: datetime


def report_client_error(uow: UnitOfWork, device: DeviceUser, error_id: str, report: ClientReport, now: datetime) -> None:
    if report.occurred_at.utcoffset() is None:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"occurred_at": "no_timezone"}})
    if report.occurred_at > now + FUTURE_SKEW:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"occurred_at": "in_future"}})
    cleaned = ClientErrorReport(
        error_id, device.user.id, device.device_id, clean_text(report.app_version), clean_text(report.os_version), clean_text(report.screen),
        clean_text(report.error_code), clean_text(report.message), report.occurred_at, now,
    )
    with uow:
        recent = uow.client_errors.recent(device.device_id, now - REPORT_WINDOW)
        if len(recent) >= REPORTS_PER_HOUR:
            oldest = recent[len(recent) - REPORTS_PER_HOUR]
            raise PriceWatcherError(ErrorCode.RATE_LIMITED, {"retry_after_seconds": max(1, math.ceil((oldest + REPORT_WINDOW - now).total_seconds()))})
        uow.client_errors.add(cleaned)
        uow.notifications.notify_admins(
            "client_error", f"client_error:{cleaned.app_version}:{cleaned.error_code}"[:200],
            {
                "error_id": error_id, "app_version": cleaned.app_version, "os_version": cleaned.os_version, "screen": cleaned.screen,
                "error_code": cleaned.error_code, "message": cleaned.message[:NOTIFICATION_MESSAGE_MAX],
            },
            now,
        )
        uow.commit()


def purge_client_errors(uow: UnitOfWork, now: datetime) -> int:
    """Стереть отчёты старше 90 суток; сколько стёрто."""
    with uow:
        purged = uow.client_errors.purge(now - KEPT_FOR)
        uow.commit()
    return purged
