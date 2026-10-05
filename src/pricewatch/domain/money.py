"""Деньги и изменения: целые копейки и готовые строки для показа.

Суммы — только целые копейки (`int`), только RUB; дробные числа отклоняются.
Вид строк (DEVELOPMENT_ARCHITECTURE.md, раздел 9):
разряды — узкий неразрывный пробел, перед «₽» — неразрывный пробел, копейки
только ненулевые, минус — знак «−». Процент — всегда один знак после запятой,
половины округляются вверх; считается целочисленно, без float.
"""

from dataclasses import dataclass
from enum import StrEnum

GROUP_SEPARATOR = " "  # узкий неразрывный пробел между разрядами
CURRENCY_SEPARATOR = " "  # неразрывный пробел перед «₽»
MINUS = "−"


class Direction(StrEnum):
    DOWN = "down"
    UP = "up"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class Change:
    """Изменение суммы или цены за период; `change_minor` со знаком, отрицательное — подешевело."""

    direction: Direction
    change_minor: int
    change_text: str
    percent_text: str


def format_rub(amount_minor: int) -> str:
    """«12 990 ₽», «12 990,50 ₽»; отрицательная сумма — со знаком «−»."""
    _require_minor(amount_minor)
    rubles, kopecks = divmod(abs(amount_minor), 100)
    text = f"{rubles:,}".replace(",", GROUP_SEPARATOR)
    if kopecks:
        text += f",{kopecks:02d}"
    sign = MINUS if amount_minor < 0 else ""
    return f"{sign}{text}{CURRENCY_SEPARATOR}₽"


def format_change(change_minor: int) -> str:
    """«−652 ₽», «+117 ₽», «0 ₽»."""
    _require_minor(change_minor)
    sign = "+" if change_minor > 0 else ""
    return sign + format_rub(change_minor)


def percent_tenths(change_minor: int, base_minor: int) -> int:
    """Модуль изменения в десятых долях процента от базы, половины вверх: 0,05% → 1 (0,1%)."""
    _require_minor(change_minor)
    _require_minor(base_minor)
    if base_minor <= 0:
        raise ValueError("база процента должна быть положительной")
    # |change| / base × 1000, округление половин вверх: (2·x·1000 + base) // (2·base).
    return (2 * abs(change_minor) * 1000 + base_minor) // (2 * base_minor)


def format_percent(tenths: int) -> str:
    """«0,0%», «0,1%», «12,5%» — без знака: направление показывает стрелка."""
    _require_minor(tenths)
    if tenths < 0:
        raise ValueError("процент показывается по модулю")
    whole, tenth = divmod(tenths, 10)
    return f"{whole:,}".replace(",", GROUP_SEPARATOR) + f",{tenth}%"


def price_change(before_minor: int, after_minor: int) -> Change:
    """Изменение от прежней суммы к текущей; прежняя сумма должна быть положительной."""
    _require_minor(before_minor)
    _require_minor(after_minor)
    if before_minor <= 0:
        raise ValueError("изменение считается только от положительной прежней суммы")
    delta = after_minor - before_minor
    if delta < 0:
        direction = Direction.DOWN
    elif delta > 0:
        direction = Direction.UP
    else:
        direction = Direction.FLAT
    return Change(direction, delta, format_change(delta), format_percent(percent_tenths(delta, before_minor)))


def _require_minor(value: int) -> None:
    # bool — подкласс int, float и Decimal — не копейки.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"ожидались целые копейки, получено {type(value).__name__}")
