"""Цены со страниц магазинов → целые копейки, без float.

- Структурированные данные (JSON-LD, meta): число или строка в записи schema.org —
  точка как десятичный разделитель, допускается запятая и пробелы между разрядами.
  Разбор строгий: «12.990» — двенадцать рублей девяносто девять копеек, а не 12 990.
- Видимый текст: price-parser (github.com/scrapinghub/price-parser, BSD-3) понимает
  «12 990,50 ₽», «от 1 299 руб.»; такие цены только предлагаются администратору.

Больше двух знаков копеек, ноль, отрицательные и нечисловые значения — не цена.
"""

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from price_parser import Price

RUBLE_CODES = frozenset({"RUB", "RUR"})
# Явный знак рубля отдельным словом: price-parser считает рублём любую «р» внутри слова
# («Артикул 123456» → 123 456 ₽), поэтому без такого знака текст не рассматривается.
RUBLE_TOKEN = re.compile(r"₽|\bруб\b\.?|\bр\.(?!\w)|\bRU[BR]\b", re.IGNORECASE)
RUBLE_CURRENCIES = frozenset({"₽", "руб", "р", "rub", "rur"})
CANDIDATES_MAX = 5
# Разряды: обычный, неразрывный и узкий неразрывный пробелы.
DIGIT_GROUPS = re.compile(r"[ \u00a0\u202f]")
STRICT_NUMBER = re.compile(r"^\d+(\.\d+)?$")


def minor_from_decimal(amount: Decimal) -> int:
    if not amount.is_finite() or amount <= 0:
        raise ValueError("цена должна быть положительным числом")
    kopecks = amount * 100
    if kopecks != kopecks.to_integral_value():
        raise ValueError("больше двух знаков копеек")
    return int(kopecks)


def structured_price(value: object) -> int:
    """Цена из JSON-LD или meta в копейках; ValueError, если это не цена."""
    if isinstance(value, bool):
        raise ValueError("логическое значение — не цена")
    if isinstance(value, int):
        return minor_from_decimal(Decimal(value))
    if isinstance(value, Decimal):
        return minor_from_decimal(value)
    if not isinstance(value, str):
        raise ValueError("цена не числом и не строкой")
    text = DIGIT_GROUPS.sub("", value.strip())
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    if not STRICT_NUMBER.match(text):
        raise ValueError("строка не похожа на цену")
    try:
        return minor_from_decimal(Decimal(text))
    except InvalidOperation as error:
        raise ValueError("строка не похожа на цену") from error


def is_ruble(currency: str | None) -> bool:
    """Валюта из priceCurrency; отсутствие валюты на русском сайте считается рублями."""
    return currency is None or currency.strip().upper() in RUBLE_CODES


def text_candidates(texts: Iterable[str]) -> tuple[int, ...]:
    """Цены в рублях из видимого текста — по порядку появления, без повторов, не больше пяти."""
    found: list[int] = []
    for text in texts:
        if not RUBLE_TOKEN.search(text):
            continue
        price = Price.fromstring(text)
        if price.amount is None or (price.currency or "").lower().rstrip(".") not in RUBLE_CURRENCIES:
            continue
        try:
            minor = minor_from_decimal(price.amount)
        except ValueError:
            continue  # «0 ₽», «0,005 ₽» — не цена товара
        if minor not in found:
            found.append(minor)
        if len(found) == CANDIDATES_MAX:
            break
    return tuple(found)
