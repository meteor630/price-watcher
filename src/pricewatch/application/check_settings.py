"""Настройки проверки цен у администратора (SITE_DESIGN_SPEC.md, раздел 8 «Проверка»; DEVELOPMENT_ARCHITECTURE.md, разделы 6 и 8).

- Время ежедневной проверки и до двух дополнительных, по Москве («ЧЧ:ММ», без повторов): настройка `check_times`,
  планировщик подхватывает её на следующем обходе.
- Срок актуальности цены в сутках (1–30): настройка `stale_after_days`, сразу действует на итоги, графики и пометки.
- Сохранение — по версиям обеих настроек (чужая правка — VERSION_CONFLICT); ошибки — по полям; изменение пишется в журнал (audit_events).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time

from pricewatch.application.ports import AuditEvent, UnitOfWork
from pricewatch.application.price_view import STALE_AFTER_SETTING
from pricewatch.application.runs import CHECK_TIMES_SETTING, configured_times, require_admin
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import STALE_AFTER_DAYS_MAX, STALE_AFTER_DAYS_MIN, stale_after_setting
from pricewatch.domain.schedule import TIME_FORMAT, TIMES_MAX, next_slot_at

DAYS_FORMAT = re.compile(r"[0-9]{1,3}")


@dataclass(frozen=True, slots=True)
class CheckSettings:
    """Текущие значения: `times` — по возрастанию; версии 0 — настройку ещё не задавали (действуют значения по умолчанию)."""

    times: tuple[time, ...]
    times_version: int
    stale_after_days: int
    stale_version: int
    next_run_at: datetime


def check_settings(uow: UnitOfWork, user: User, now: datetime) -> CheckSettings:
    with uow:
        require_admin(uow, user.id)
        return _current(uow, now)


def save_check_settings(
    uow: UnitOfWork, user: User, *, times: Sequence[str], stale_after_days: str, times_version: int, stale_version: int, now: datetime
) -> CheckSettings:
    """`times` — поля формы по порядку: первое обязательно, пустые дополнительные пропускаются."""
    values, days = _validated(times, stale_after_days)
    with uow:
        require_admin(uow, user.id)
        before = _current(uow, now)
        uow.settings.write(CHECK_TIMES_SETTING, values, times_version, user.id, now)
        uow.settings.write(STALE_AFTER_SETTING, days, stale_version, user.id, now)
        saved = _current(uow, now)
        uow.audit.add(AuditEvent(user.id, "check_settings_changed", "settings", None, _audit(before), _audit(saved), now))
        uow.commit()
    return saved


def _current(uow: UnitOfWork, now: datetime) -> CheckSettings:
    times_setting = uow.settings.read(CHECK_TIMES_SETTING)
    stale_setting = uow.settings.read(STALE_AFTER_SETTING)
    times = configured_times(uow)
    stale = stale_after_setting(None if stale_setting is None else stale_setting.value)
    return CheckSettings(
        times=times,
        times_version=0 if times_setting is None else times_setting.version,
        stale_after_days=stale.days,
        stale_version=0 if stale_setting is None else stale_setting.version,
        next_run_at=next_slot_at(times, now),
    )


def _audit(settings: CheckSettings) -> dict[str, object]:
    return {"check_times": [at.strftime("%H:%M") for at in settings.times], "stale_after_days": settings.stale_after_days}


def _validated(times: Sequence[str], stale_after_days: str) -> tuple[list[str], int]:
    """Значения для записи или VALIDATION_ERROR со всеми ошибками по полям (check_time_1…3, stale_after_days)."""
    fields: dict[str, str] = {}
    values: list[str] = []
    if len(times) != TIMES_MAX:
        raise ValueError(f"форма передаёт ровно {TIMES_MAX} поля времени")
    for index, raw in enumerate(times, start=1):
        value = raw.strip()
        name = f"check_time_{index}"
        if not value:
            if index == 1:
                fields[name] = "required"
            continue
        if not value.isascii() or TIME_FORMAT.match(value) is None:
            fields[name] = "invalid"
        elif value in values:
            fields[name] = "duplicate"
        else:
            values.append(value)
    raw_days = stale_after_days.strip()
    days = int(raw_days) if DAYS_FORMAT.fullmatch(raw_days) else 0
    if not STALE_AFTER_DAYS_MIN <= days <= STALE_AFTER_DAYS_MAX:
        fields["stale_after_days"] = "out_of_range"
    if fields:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": fields})
    return sorted(values), days
