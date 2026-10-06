"""SVG-графики: монотонная кривая без ложных выбросов, разрывы на пропусках, ровная цена посередине."""

import re
from datetime import date, timedelta

import pytest

from pricewatch.domain.charts import ChartPoint, PointStatus
from pricewatch.web.charts import CHART_HEIGHT, CHART_WIDTH, PADDING_Y, VIEW_HEIGHT, VIEW_WIDTH, chart, plot


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


# --- большой график ---



def test_chart_grid_is_round_rubles_top_to_bottom() -> None:
    drawn = chart(points(186_250_00, 186_250_00, 194_094_00, 194_094_00))
    assert drawn is not None

    labels = [label for _, label in drawn.grid]
    assert labels == ["196 000", "194 000", "192 000", "190 000", "188 000", "186 000"]
    assert drawn.grid[0][0] == "0" and drawn.grid[-1][0] == str(CHART_HEIGHT)
    assert drawn.view_box == f"0 0 {CHART_WIDTH} {CHART_HEIGHT}"


def test_chart_dates_hits_and_today_point() -> None:
    drawn = chart(points(*[1000_00 + index * 100 for index in range(30)]))
    assert drawn is not None

    assert [(label.text, label.anchor) for label in drawn.x_labels] == [("01.10", "start"), ("11.10", "middle"), ("20.10", "middle"), ("30.10", "end")]
    assert drawn.x_labels[0].percent == "0%" and drawn.x_labels[-1].percent == "100%"
    assert len(drawn.hits) == 30 and drawn.hits[0].x == "0" and drawn.hits[0].label == "подпись"
    assert drawn.last[0] == "100%"
    assert drawn.summary.startswith("С 01.10 по 30.10: от 1 000 до 1 029")


def test_chart_flat_price_in_middle_and_gaps() -> None:
    flat = chart(points(500_00, 500_00))
    gappy = chart(points(100_00, None, 300_00))
    assert flat is not None and gappy is not None

    assert flat.last[1] == "50%"
    assert len(gappy.lines) == 2 and len(gappy.areas) == 2 and gappy.hits[1].y_percent is None


def test_chart_axis_never_goes_below_zero() -> None:
    drawn = chart(points(50, 50))
    assert drawn is not None

    assert all(not label.startswith("-") for _, label in drawn.grid)


def test_chart_without_history() -> None:
    assert chart(points(None, None)) is None
