"""Настройки проверки цен у администратора (SITE_DESIGN_SPEC.md, раздел 8 «Проверка»; DEVELOPMENT_ARCHITECTURE.md, разделы 6 и 8).

- Время ежедневной проверки и до двух дополнительных, по Москве («ЧЧ:ММ», без повторов): настройка `check_times`,
  планировщик подхватывает её на следующем обходе.
- Срок актуальности цены в сутках (1–30): настройка `stale_after_days`, сразу действует на итоги, графики и пометки.
- Пороги подозрительных изменений: схожесть названия 50–99 % и скачок цены 5–200 % (решение владельца 07.10.2026).
  Действуют и при приёме результата worker, и в расчёте цены каждого пользователя: его нерешённые наблюдения
  пересчитываются по новому порогу, принятые и отклонённые остаются как решены.
- Сохранение — по версиям всех настроек (чужая правка — VERSION_CONFLICT); ошибки — по полям; изменение пишется в журнал (audit_events).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time

from pricewatch.application.ports import AuditEvent, UnitOfWork
from pricewatch.application.price_view import PRICE_JUMP_SETTING, STALE_AFTER_SETTING, TITLE_SIMILARITY_SETTING, review_thresholds
from pricewatch.application.runs import CHECK_TIMES_SETTING, configured_times, require_admin
from pricewatch.domain.accounts import User
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import STALE_AFTER_DAYS_MAX, STALE_AFTER_DAYS_MIN, stale_after_setting
from pricewatch.domain.review import PRICE_JUMP_RANGE, TITLE_SIMILARITY_RANGE
from pricewatch.domain.schedule import TIME_FORMAT, TIMES_MAX, next_slot_at

SETTING_KEYS = (CHECK_TIMES_SETTING, STALE_AFTER_SETTING, TITLE_SIMILARITY_SETTING, PRICE_JUMP_SETTING)
NUMBER_FORMAT = re.compile(r"[0-9]{1,3}")
# Поле формы → (настройка, допустимый диапазон).
NUMBER_FIELDS = {
    "stale_after_days": (STALE_AFTER_SETTING, (STALE_AFTER_DAYS_MIN, STALE_AFTER_DAYS_MAX)),
    "title_similarity_percent": (TITLE_SIMILARITY_SETTING, TITLE_SIMILARITY_RANGE),
    "price_jump_percent": (PRICE_JUMP_SETTING, PRICE_JUMP_RANGE),
}


@dataclass(frozen=True, slots=True)
class CheckSettings:
    """Текущие значения: `times` — по возрастанию; версия 0 — настройку ещё не задавали (действует значение по умолчанию)."""

    times: tuple[time, ...]
    stale_after_days: int
    title_similarity_percent: int
    price_jump_percent: int
    versions: dict[str, int]
    next_run_at: datetime


@dataclass(frozen=True, slots=True)
class CheckForm:
    """Введённое в форму как есть: `times` — поля времени по порядку, числа — строками, версии — по ключам настроек."""

    times: Sequence[str]
    numbers: dict[str, str]
    versions: dict[str, int]


def check_settings(uow: UnitOfWork, user: User, now: datetime) -> CheckSettings:
    with uow:
        require_admin(uow, user.id)
        return _current(uow, now)


def save_check_settings(uow: UnitOfWork, user: User, form: CheckForm, now: datetime) -> CheckSettings:
    """Первое время обязательно, пустые дополнительные пропускаются; все ошибки — разом, по полям."""
    values = _validated(form)
    with uow:
        require_admin(uow, user.id)
        before = _current(uow, now)
        for key in SETTING_KEYS:
            uow.settings.write(key, values[key], form.versions[key], user.id, now)
        saved = _current(uow, now)
        uow.audit.add(AuditEvent(user.id, "check_settings_changed", "settings", None, _audit(before), _audit(saved), now))
        uow.commit()
    return saved


def _current(uow: UnitOfWork, now: datetime) -> CheckSettings:
    stored = {key: uow.settings.read(key) for key in SETTING_KEYS}
    times = configured_times(uow)
    stale = stored[STALE_AFTER_SETTING]
    thresholds = review_thresholds(uow)
    return CheckSettings(
        times=times,
        stale_after_days=stale_after_setting(None if stale is None else stale.value).days,
        title_similarity_percent=thresholds.title_similarity_min_percent,
        price_jump_percent=thresholds.price_jump_max_percent,
        versions={key: 0 if setting is None else setting.version for key, setting in stored.items()},
        next_run_at=next_slot_at(times, now),
    )


def _audit(settings: CheckSettings) -> dict[str, object]:
    return {
        CHECK_TIMES_SETTING: [at.strftime("%H:%M") for at in settings.times],
        STALE_AFTER_SETTING: settings.stale_after_days,
        TITLE_SIMILARITY_SETTING: settings.title_similarity_percent,
        PRICE_JUMP_SETTING: settings.price_jump_percent,
    }


def _validated(form: CheckForm) -> dict[str, object]:
    """Значения для записи по ключам настроек или VALIDATION_ERROR со всеми ошибками по полям."""
    if len(form.times) != TIMES_MAX or set(form.versions) != set(SETTING_KEYS) or set(form.numbers) != set(NUMBER_FIELDS):
        raise ValueError("форма передаёт все поля настроек проверки")
    fields: dict[str, str] = {}
    times: list[str] = []
    for index, raw in enumerate(form.times, start=1):
        value = raw.strip()
        name = f"check_time_{index}"
        if not value:
            if index == 1:
                fields[name] = "required"
            continue
        if not value.isascii() or TIME_FORMAT.match(value) is None:
            fields[name] = "invalid"
        elif value in times:
            fields[name] = "duplicate"
        else:
            times.append(value)
    values: dict[str, object] = {CHECK_TIMES_SETTING: sorted(times)}
    for name, (key, (low, high)) in NUMBER_FIELDS.items():
        raw = form.numbers[name].strip()
        number = int(raw) if NUMBER_FORMAT.fullmatch(raw) else low - 1
        if not low <= number <= high:
            fields[name] = "out_of_range"
        values[key] = number
    if fields:
        raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": fields})
    return values
