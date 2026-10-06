"""Шаблоны сайта на Jinja2 (SITE_DESIGN_SPEC.md, раздел 13; решение 06.10.2026).

- Автоэкранирование включено для всех шаблонов: данные пользователя и магазинов не становятся разметкой.
- Неизвестная переменная в шаблоне — ошибка, а не пустая строка: опечатка не превращается в тихий пробел.
- Адрес статики получает метку содержимого (`?v=…`): после обновления браузер берёт новый файл.
"""

import hashlib
from functools import cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
STATIC_PREFIX = "/static/"


@cache
def static_url(path: str) -> str:
    """Адрес файла статики с меткой содержимого; файла нет — ошибка сборки страницы."""
    target = (STATIC_DIR / path).resolve()
    if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
        raise FileNotFoundError(f"нет файла статики: {path}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
    return f"{STATIC_PREFIX}{path}?v={digest}"


environment = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
environment.globals["static"] = static_url


def render(name: str, **context: object) -> str:
    return environment.get_template(name).render(**context)
