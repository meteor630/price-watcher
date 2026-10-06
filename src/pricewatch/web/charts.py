"""Графики сайта как SVG с сервера (решение 06.10.2026): плавная линия без ложных выбросов.

Маленький график карточки — `plot`. Большой график страницы — `chart`: сетка с «круглыми» суммами,
подписи дат, заливка под линией, точка «сегодня» и области наведения с готовой подписью точки.
Подписи сумм и дат не рисуются внутри растягиваемого SVG (текст исказился бы): суммы стоят колонкой
напротив линий сетки, даты и точка — в процентах ширины.

- Линия — монотонная кубическая интерполяция (Fritsch–Carlson): между двумя ценами кривая
  не уходит выше большей и ниже меньшей, поэтому график не рисует цен, которых не было.
- Дни без значения разрывают линию: пропуск виден, его не замазывает прямая.
- Координаты в условной системе `viewBox`; толщину линии держит CSS (vector-effect),
  поэтому график растягивается по ширине карточки без искажения линии.
- Ровная цена — линия посередине высоты, а не по нижнему краю.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from pricewatch.domain.charts import ChartPoint
from pricewatch.domain.money import GROUP_SEPARATOR

VIEW_WIDTH = 300
VIEW_HEIGHT = 64
PADDING_Y = 6


@dataclass(frozen=True, slots=True)
class Plot:
    view_box: str
    # Отрезки линии (между пропусками) в синтаксисе SVG path.
    lines: tuple[str, ...]


def plot(points: Sequence[ChartPoint], width: int = VIEW_WIDTH, height: int = VIEW_HEIGHT) -> Plot | None:
    """`None` — истории нет (нет ни одного значения)."""
    values = [point.value_minor for point in points]
    known = [value for value in values if value is not None]
    if not known:
        return None
    low, high = min(known), max(known)
    step = Fraction(width, max(len(points) - 1, 1))

    def y(value: int) -> Fraction:
        if high == low:
            return Fraction(height, 2)
        usable = height - 2 * PADDING_Y
        return PADDING_Y + Fraction(high - value, high - low) * usable

    segments: list[list[tuple[Fraction, Fraction]]] = [[]]
    for index, value in enumerate(values):
        if value is None:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append((index * step if len(points) > 1 else Fraction(width, 2), y(value)))
    segments = [segment for segment in segments if segment]
    return Plot(f"0 0 {width} {height}", tuple(_path(segment) for segment in segments))


def _path(segment: Sequence[tuple[Fraction, Fraction]]) -> str:
    if len(segment) == 1:
        x, y = segment[0]
        return f"M{_n(x)} {_n(y)} h0.01"
    tangents = _monotone_tangents(segment)
    parts = [f"M{_n(segment[0][0])} {_n(segment[0][1])}"]
    for index in range(len(segment) - 1):
        (x0, y0), (x1, y1) = segment[index], segment[index + 1]
        third = (x1 - x0) / 3
        parts.append(
            f"C{_n(x0 + third)} {_n(y0 + tangents[index] * third)} {_n(x1 - third)} {_n(y1 - tangents[index + 1] * third)} {_n(x1)} {_n(y1)}"
        )
    return " ".join(parts)


def _monotone_tangents(segment: Sequence[tuple[Fraction, Fraction]]) -> list[Fraction]:
    """Касательные Fritsch–Carlson: кривая монотонна там, где монотонны данные."""
    slopes = [(y1 - y0) / (x1 - x0) for (x0, y0), (x1, y1) in zip(segment, segment[1:], strict=False)]
    tangents = [slopes[0]] + [
        Fraction(0) if left * right <= 0 else (left + right) / 2 for left, right in zip(slopes, slopes[1:], strict=False)
    ] + [slopes[-1]]
    for index, slope in enumerate(slopes):
        if slope == 0:
            tangents[index] = tangents[index + 1] = Fraction(0)
            continue
        a, b = tangents[index] / slope, tangents[index + 1] / slope
        total = a * a + b * b
        if total > 9:
            # Ограничение длины касательных: иначе кривая проскакивает за соседнюю цену.
            scale = Fraction(3) / _sqrt(total)
            tangents[index], tangents[index + 1] = scale * a * slope, scale * b * slope
    return tangents


def _sqrt(value: Fraction) -> Fraction:
    return Fraction(float(value) ** 0.5).limit_denominator(10_000)


def _n(value: Fraction) -> str:
    """Координата с двумя знаками: короче разметка, точности для экрана достаточно."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


CHART_WIDTH = 1000
CHART_HEIGHT = 300
X_LABELS = 4
NICE_STEPS = (1, 2, 5)


@dataclass(frozen=True, slots=True)
class AxisLabel:
    percent: str
    text: str
    anchor: str


@dataclass(frozen=True, slots=True)
class Hit:
    """Полоса наведения одного дня: координаты в системе viewBox, подпись точки и высота значения в процентах."""

    x: str
    width: str
    label: str
    y_percent: str | None


@dataclass(frozen=True, slots=True)
class Chart:
    view_box: str
    lines: tuple[str, ...]
    areas: tuple[str, ...]
    # Линии сетки сверху вниз: координата y в viewBox и подпись суммы для колонки слева.
    grid: tuple[tuple[str, str], ...]
    x_labels: tuple[AxisLabel, ...]
    hits: tuple[Hit, ...]
    last: tuple[str, str]
    summary: str


def chart(points: Sequence[ChartPoint]) -> Chart | None:
    """`None` — истории нет."""
    known = [point.value_minor for point in points if point.value_minor is not None]
    if not known:
        return None
    low, high, step = _scale(min(known), max(known))
    count = len(points)
    step_x = Fraction(CHART_WIDTH, max(count - 1, 1))

    def x(index: int) -> Fraction:
        return index * step_x if count > 1 else Fraction(CHART_WIDTH, 2)

    def y(value: int) -> Fraction:
        return Fraction(high - value, high - low) * CHART_HEIGHT

    segments: list[list[tuple[Fraction, Fraction]]] = [[]]
    for index, point in enumerate(points):
        if point.value_minor is None:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append((x(index), y(point.value_minor)))
    segments = [segment for segment in segments if segment]
    lines = tuple(_path(segment) for segment in segments)
    areas = tuple(f"{_path(segment)} L{_n(segment[-1][0])} {CHART_HEIGHT} L{_n(segment[0][0])} {CHART_HEIGHT} Z" for segment in segments)
    grid = tuple((_n(y(value)), _axis_rub(value)) for value in range(high, low - 1, -step))
    shown = sorted({round(i * (count - 1) / (X_LABELS - 1)) for i in range(X_LABELS)}) if count > 1 else [0]
    x_labels = tuple(
        AxisLabel(_percent(x(index), CHART_WIDTH), f"{points[index].day:%d.%m}", "start" if index == 0 else "end" if index == count - 1 else "middle")
        for index in shown
    )
    half = step_x / 2
    hits = tuple(
        Hit(
            _n(max(x(index) - half, Fraction(0))),
            _n(min(x(index) + half, Fraction(CHART_WIDTH)) - max(x(index) - half, Fraction(0))),
            point.label,
            None if point.value_minor is None else _percent(y(point.value_minor), CHART_HEIGHT),
        )
        for index, point in enumerate(points)
    )
    last_x, last_y = segments[-1][-1]
    first, last = points[0], points[-1]
    summary = f"С {first.day:%d.%m} по {last.day:%d.%m}: от {_axis_rub(min(known))} до {_axis_rub(max(known))} ₽"
    return Chart(
        f"0 0 {CHART_WIDTH} {CHART_HEIGHT}", lines, areas, grid, x_labels, hits,
        (_percent(last_x, CHART_WIDTH), _percent(last_y, CHART_HEIGHT)), summary,
    )


def _scale(low_minor: int, high_minor: int) -> tuple[int, int, int]:
    """Границы и шаг сетки «круглыми» рублями: 3–6 делений; ровная цена — посередине."""
    span = max(high_minor - low_minor, 1)
    magnitude = 100
    while True:
        for factor in NICE_STEPS:
            step = factor * magnitude
            if span <= 5 * step:
                low = (low_minor // step) * step
                high = -((-high_minor) // step) * step
                if low == high:
                    low, high = max(low - step, 0), high + step
                return low, high, step
        magnitude *= 10


def _axis_rub(amount_minor: int) -> str:
    return f"{amount_minor // 100:,}".replace(",", GROUP_SEPARATOR)


def _percent(value: Fraction, whole: int) -> str:
    return f"{_n(value * 100 / whole)}%"
