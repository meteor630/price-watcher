"""Вырезание секретов из данных журнала.

Три уровня:
- поля с чувствительным именем (password, token, cookie…) заменяются целиком;
- в тексте вырезаются Bearer-токены, логин и пароль в адресах, значения
  чувствительных параметров URL и заголовков Authorization и Cookie;
- значения настоящих секретов, зарегистрированные при загрузке настроек,
  вырезаются дословно где угодно — так ловится и голый пароль в тексте ошибки.
"""

import re
from collections.abc import Mapping
from urllib.parse import quote, quote_plus

MASK = "[скрыто]"

_SENSITIVE_NAME = re.compile(
    r"pass(word|wd)?|token|secret|authorization|cookie|session|api_?key|pairing_code|credential",
    re.IGNORECASE,
)
_SECRET_PARAM = (
    r"(?:[a-z0-9_]*(?:pass(?:word|wd)?|token|secret|session|api_?key|signature)[a-z0-9_]*"
    r"|code|pairing_code|sig)"
)
_TEXT_PATTERNS = [
    # Authorization: Bearer xxx, Cookie: a=b; c=d — до конца строки
    (re.compile(r"(?i)\b((?:proxy-)?authorization|set-cookie|cookie)(\s*[:=]\s*)[^\n]+"), r"\1\2" + MASK),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 " + MASK),
    # scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + MASK + "@"),
    # ?token=…&password=… в адресах и текст вида key=value; error_code и подобные не трогаются
    (re.compile(rf"(?i)(^|[?&;\s])({_SECRET_PARAM})=([^&\s;#'\"]+)"), r"\1\2=" + MASK),
]


_registered_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Запомнить значение секрета; оно и его URL-кодированные формы вырезаются из любого текста."""
    if value:
        _registered_secrets.update({value, quote(value, safe=""), quote_plus(value)})


def is_sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE_NAME.search(name))


def redact_text(text: str) -> str:
    # Длинные значения первыми: короткий секрет может быть частью длинного.
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        text = text.replace(secret, MASK)
    for pattern, replacement in _TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_mapping(mapping: Mapping[object, object]) -> dict[str, object]:
    return {
        str(key): MASK if is_sensitive_name(str(key)) else redact_value(item) for key, item in mapping.items()
    }


def redact_value(value: object) -> object:
    """Копия значения без секретов; словари и списки обходятся рекурсивно."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(repr(value))
