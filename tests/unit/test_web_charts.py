"""SVG-графики: монотонная кривая без ложных выбросов, разрывы на пропусках, ровная цена посередине."""

import re
from datetime import date, timedelta

import pytest

from pricewatch.domain.charts import ChartPoint, PointStatus
from pricewatch.web.charts import PADDING_Y, VIEW_HEIGHT, VIEW_WIDTH, plot


def points(*values: int | None) -> list[ChartPoint]:
    start = date(2026, 10, 1)
    return [
        ChartPoint(start + timedelta(days=index), value, "подпись", PointStatus.OK if value is not None else PointStatus.NO_DATA)
        for index, value in enumerate(values)
    ]


def numbers(path: str) -> list[float]:
    return [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path)]


def test_no_history_is_no_chart() -> None:
    assert plot(points(None, None, None)) is None
    assert plot([]) is None


def test_curve_never_leaves_range_of_neighbouring_prices() -> None:
    chart = plot(points(100, 900, 120, 130, 1000, 1000, 50))
    assert chart is not None

    path = chart.lines[0]
    start = numbers(path.split("C")[0])
    previous_y = start[1]
    for command in path.split("C")[1:]:
        _, c1y, _, c2y, _, end_y = numbers(command)
        low, high = sorted((previous_y, end_y))
        assert low - 1e-6 <= c1y <= high + 1e-6 and low - 1e-6 <= c2y <= high + 1e-6
        previous_y = end_y


def test_highest_price_is_at_top_lowest_at_bottom() -> None:
    chart = plot(points(500, 1000))
    assert chart is not None

    ys = numbers(chart.lines[0])
    assert ys[1] == VIEW_HEIGHT - PADDING_Y and ys[-1] == PADDING_Y  # дорожает — линия идёт вверх
    assert chart.view_box == f"0 0 {VIEW_WIDTH} {VIEW_HEIGHT}"


def test_flat_price_is_drawn_in_the_middle() -> None:
    chart = plot(points(700, 700, 700))
    assert chart is not None

    assert set(numbers(chart.lines[0])[1::2]) == {VIEW_HEIGHT / 2}


def test_gap_breaks_the_line() -> None:
    chart = plot(points(100, 200, None, None, 300, 250))
    assert chart is not None

    assert len(chart.lines) == 2
    assert numbers(chart.lines[1])[-2] == VIEW_WIDTH  # второй отрезок доходит до сегодняшнего дня


def test_single_value_is_a_dot() -> None:
    chart = plot(points(None, 400, None))
    assert chart is not None

    assert chart.lines == (f"M{VIEW_WIDTH // 2} {VIEW_HEIGHT // 2} h0.01",)


@pytest.mark.parametrize("count", [7, 30, 365])
def test_ranges_span_full_width(count: int) -> None:
    chart = plot(points(*[1000 + index % 5 for index in range(count)]))
    assert chart is not None

    xs = numbers(chart.lines[0])[0::2]
    assert xs[0] == 0 and xs[-1] == VIEW_WIDTH
