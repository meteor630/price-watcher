"""Колокольчик администратора (SITE_DESIGN_SPEC.md, раздел 3; DEVELOPMENT_ARCHITECTURE.md, раздел 10).

Системные уведомления — только администраторам: текст, время последнего повтора, счётчик повторов
и ссылка на раздел. Одинаковый инцидент — одна строка (dedup_key), повтор снова делает её непрочитанной.
Неожиданное исключение на границе HTTP тоже становится уведомлением (report_unexpected); его запись
не должна мешать ответу пользователю — сбой записи пишет в журнал вызывающий.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pricewatch.application.ports import BellNotification, UnitOfWork
from pricewatch.application.runs import require_admin
from pricewatch.domain.accounts import User
from pricewatch.domain.days import MOSCOW

PAGE_MAX = 100
READ_IDS_MAX = 100
PAUSE_REASONS = {"CAPTCHA": "магазин запросил проверку «я не робот»", "BLOCKED": "магазин ограничил доступ"}
SILENCE_TEXTS = {
    "worker_silent": "Worker «{name}» молчит с {since}: цены не проверяются.",
    "worker_back": "Worker «{name}» снова на связи (молчал с {since}).",
    "bot_silent": "Telegram-бот «{name}» молчит с {since}: сообщения в Telegram не уходят.",
    "bot_back": "Telegram-бот «{name}» снова на связи (молчал с {since}).",
}
BACKUP_FAILURES = {
    "DATABASE_DUMP_FAILED": "не снят дамп базы",
    "ENCRYPTION_FAILED": "копия не зашифрована",
    "STORAGE_FAILED": "нет доступа к папке копий",
    "MEDIA_UNAVAILABLE": "нет папки картинок",
    "RETENTION_FAILED": "копия сделана, но старые не удалены",
    "UPLOAD_FAILED": "копия сделана, но не отправлена на арендный сервер",
}


@dataclass(frozen=True, slots=True)
class BellItem:
    notification_id: int
    kind: str
    text: str
    created_at: datetime
    read: bool
    repeat_count: int
    # Раздел сайта, где разбираться с причиной; None — переходить некуда.
    web_href: str | None


@dataclass(frozen=True, slots=True)
class BellPage:
    items: list[BellItem]
    unread_count: int
    next_offset: int | None


def bell_page(uow: UnitOfWork, user: User, offset: int, limit: int) -> BellPage:
    with uow:
        require_admin(uow, user.id)
        found = uow.notifications.bell(user.id, offset, limit + 1)
        unread = uow.notifications.unread_count(user.id)
    items = [_item(notification) for notification in found[:limit]]
    return BellPage(items, unread, offset + limit if len(found) > limit else None)


def mark_read(uow: UnitOfWork, user: User, notification_ids: Sequence[int] | None, now: datetime) -> int:
    """Отметить прочитанными (None — все); остаток непрочитанных."""
    with uow:
        require_admin(uow, user.id)
        uow.notifications.mark_read(user.id, notification_ids, now)
        unread = uow.notifications.unread_count(user.id)
        uow.commit()
    return unread


def report_unexpected(uow: UnitOfWork, error_id: str, method: str, route: str, exception: str, now: datetime) -> None:
    """Уведомление администраторам о неожиданном исключении; повторы одного места склеиваются."""
    with uow:
        uow.notifications.notify_admins(
            "unexpected_error", f"unexpected_error:{exception}:{method} {route}"[:200],
            {"error_id": error_id, "method": method, "route": route, "exception": exception}, now,
        )
        uow.commit()


def _item(notification: BellNotification) -> BellItem:
    text, href = notification_text(notification.kind, notification.payload)
    return BellItem(notification.notification_id, notification.kind, text, notification.created_at, notification.read, notification.repeat_count, href)


def notification_text(kind: str, payload: dict[str, object]) -> tuple[str, str | None]:
    """Текст уведомления и раздел сайта для перехода — общий для колокольчика и Telegram."""
    if kind == "store_paused":
        until = datetime.fromisoformat(str(payload["paused_until"])).astimezone(MOSCOW)
        reason = PAUSE_REASONS.get(str(payload["reason"]), str(payload["reason"]))
        return f"«{payload['store']}» на паузе до {until:%d.%m в %H:%M}: {reason}.", "/web/settings/stores"
    if kind == "unexpected_error":
        return f"Неожиданная ошибка сервера: {payload['exception']} в {payload['method']} {payload['route']}. Номер ошибки {payload['error_id']}.", None
    if kind == "client_error":
        text = f"Ошибка приложения {payload['app_version']} ({payload['os_version']}) на экране «{payload['screen']}»: {payload['error_code']}"
        message = str(payload["message"])
        return f"{text}{' — ' + message if message else ''}. Номер ошибки {payload['error_id']}.", None
    if kind == "run_summary":
        return ("Проверка завершена: изменения есть." if payload["changed"] else "Проверка завершена: изменений нет."), "/web/"
    if kind == "backup_failed":
        started = datetime.fromisoformat(str(payload["started_at"])).astimezone(MOSCOW)
        reason = BACKUP_FAILURES.get(str(payload["error_code"]), str(payload["error_code"]))
        return f"Резервная копия {started:%d.%m в %H:%M} не удалась: {reason}. Журнал — служба backup на домашнем узле.", None
    if kind == "backup_stale":
        since = datetime.fromisoformat(str(payload["since"])).astimezone(MOSCOW)
        what = "Удачной резервной копии нет с" if payload["ever_ok"] else "Ни одной удачной резервной копии с первой попытки"
        return f"{what} {since:%d.%m в %H:%M}: проверьте службу backup на домашнем узле.", None
    if kind in SILENCE_TEXTS:
        since = datetime.fromisoformat(str(payload["since"])).astimezone(MOSCOW)
        return SILENCE_TEXTS[kind].format(name=payload["name"], since=f"{since:%d.%m в %H:%M}"), None
    # Вид уведомления открыт: незнакомый показывается нейтрально, а не пропадает.
    return f"Системное событие «{kind}».", None
