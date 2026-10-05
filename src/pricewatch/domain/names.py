"""Имена категорий, групп и товаров: очистка для показа и ключ для сравнения.

Отображаемое имя: Unicode NFC, пробелы по краям обрезаны, любые пробелы внутри
сведены к одному. Ключ сравнения дополнительно приводит совместимые символы
(NFKC: полноширинные буквы, лигатуры), регистр (casefold) и «ё» к «е». Поэтому
«Ёлка», «ЁЛКА» и «елка  » — одно имя, а опечатка в регистре или пробеле
не создаёт дубль (DEVELOPMENT_ARCHITECTURE.md, раздел 5).
"""

import unicodedata

from pricewatch.domain.errors import ErrorCode, PriceWatcherError

CATEGORY_NAME_MAX = 100
GROUP_NAME_MAX = 100
ITEM_NAME_MAX = 200
ITEM_FULL_NAME_MAX = 500
QUANTITY_MIN = 1
QUANTITY_MAX = 999


def clean_name(raw: str, *, field: str, max_length: int) -> str:
    """Имя для хранения и показа или VALIDATION_ERROR с причиной по полю."""
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not name:
        raise _invalid(field, "empty")
    if any(unicodedata.category(char) == "Cc" for char in name):
        raise _invalid(field, "invalid_characters")
    if len(name) > max_length:
        raise _invalid(field, "too_long")
    return name


def name_key(name: str) -> str:
    """Ключ сравнения имён на уникальность."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(folded.replace("ё", "е").split())


def check_quantity(value: int, *, field: str = "quantity") -> int:
    # bool — подкласс int, но True не количество.
    if isinstance(value, bool) or not isinstance(value, int) or not QUANTITY_MIN <= value <= QUANTITY_MAX:
        raise _invalid(field, "out_of_range")
    return value


def _invalid(field: str, reason: str) -> PriceWatcherError:
    return PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {field: reason}})
