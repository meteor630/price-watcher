"""Шаблоны сайта на Jinja2 (SITE_DESIGN_SPEC.md, раздел 13; решение 06.10.2026).

- Автоэкранирование включено для всех шаблонов: данные пользователя и магазинов не становятся разметкой.
- Неизвестная переменная в шаблоне — ошибка, а не пустая строка: опечатка не превращается в тихий пробел.
- Адрес статики получает метку содержимого (`?v=…`): после обновления браузер берёт новый файл.
"""

import hashlib
from functools import cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from pricewatch.web.charts import chart, plot
from pricewatch.domain.days import Period
from pricewatch.web.formats import (
    ARROWS, CHART_RANGE_NAMES, PERIOD_NAMES, PERIOD_SINCE, STATUS_LABELS, count_word, long_date, moscow_moment, period_index, run_line, swapped_order,
)

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
environment.globals.update(
    static=static_url,
    plot=plot,
    chart=chart,
    status_labels=STATUS_LABELS,
    chart_range_names=CHART_RANGE_NAMES,
    long_date=long_date,
    moscow_moment=moscow_moment,
    period_day=Period.DAY,
    swapped_order=swapped_order,
    count_word=count_word,
    period_index=period_index,
    run_line=run_line,
    period_names=PERIOD_NAMES,
    period_since=PERIOD_SINCE,
    arrows=ARROWS,
)


def render(template: str, /, **context: object) -> str:
    """Имя шаблона — только позиционно: переменные шаблона могут называться как угодно (в том числе name)."""
    return environment.get_template(template).render(**context)
