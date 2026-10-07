"""Отчёт о неожиданной ошибке приложения (DEVELOPMENT_ARCHITECTURE.md, раздел 10, «Отчёты об ошибках приложения»).

Отчёт — недоверенный ввод. Перед хранением из текста вырезаются то, что может быть секретом:
параметры и фрагменты адресов, заголовок Bearer, пары «ключ=значение» с чувствительными именами
и длинные строки, похожие на токены. Управляющие символы (кроме перевода строки) убираются.
Лимит — 20 отчётов в час на устройство; хранится 90 суток.
"""

import re
import unicodedata
from datetime import timedelta

REPORTS_PER_HOUR = 20
REPORT_WINDOW = timedelta(hours=1)
KEPT_FOR = timedelta(days=90)
# Часы устройства могут спешить; отчёт «из будущего» дальше этого — ошибка ввода.
FUTURE_SKEW = timedelta(minutes=10)
HIDDEN = "[скрыто]"

_URL_TAIL = re.compile(r"(?P<base>https?://[^\s?#]+)[?#]\S*", re.IGNORECASE)
_BEARER = re.compile(r"(?P<scheme>bearer)\s+\S+", re.IGNORECASE)
# Значение «Bearer …» уже обработано выше: пара его не трогает, чтобы слово схемы осталось.
_SECRET_PAIR = re.compile(r"(?P<name>[a-z_]*(?:token|password|passwd|secret|key|code|session|cookie|auth)[a-z_]*)\s*[=:]\s*(?!bearer\b)\S+", re.IGNORECASE)
# Без «/» и «.»: иначе под токен попадает обычный путь адреса; части JWT между точками скрываются по отдельности.
_TOKEN_LIKE = re.compile(r"[A-Za-z0-9_\-+=]{32,}")


def clean_text(raw: str) -> str:
    """Текст отчёта для хранения: без секретов и управляющих символов, обрезанный по краям."""
    text = "".join(char for char in raw if char == "\n" or unicodedata.category(char) not in {"Cc", "Cf"})
    text = _URL_TAIL.sub(lambda match: match.group("base"), text)
    text = _BEARER.sub(lambda match: f"{match.group('scheme')} {HIDDEN}", text)
    text = _SECRET_PAIR.sub(lambda match: f"{match.group('name')}={HIDDEN}", text)
    text = _TOKEN_LIKE.sub(HIDDEN, text)
    return text.strip()
