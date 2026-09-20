from datetime import datetime, timedelta

from forecasting.model import forecast_load

STEP_SECONDS = 15


def make_points(values: list[float], start: datetime | None = None):
    start = start or datetime(2026, 1, 1)
    return [(start + timedelta(seconds=i * STEP_SECONDS), v) for i, v in enumerate(values)]


def test_too_few_points_returns_zero_confidence():
    points = make_points([10.0, 12.0, 11.0])  # well under MIN_DATA_POINTS

    result = forecast_load(points, horizon_seconds=300, step_seconds=STEP_SECONDS)

    assert result.confidence == 0.0
    assert result.predicted_value == 0.0


def test_flat_series_gives_high_confidence_and_matches_level():
    points = make_points([50.0] * 30)

    result = forecast_load(points, horizon_seconds=300, step_seconds=STEP_SECONDS)

    assert result.confidence > 0.9
    assert abs(result.predicted_value - 50.0) < 1.0


def test_clearly_rising_trend_forecasts_higher_than_last_observed_value():
    points = make_points([float(i) for i in range(30)])  # 0, 1, 2, ... 29

    result = forecast_load(points, horizon_seconds=300, step_seconds=STEP_SECONDS)

    assert result.predicted_value > points[-1][1]


def test_negative_forecast_is_clamped_to_zero():
    points = make_points([float(30 - i) for i in range(30)])  # steep downward trend

    result = forecast_load(points, horizon_seconds=3000, step_seconds=STEP_SECONDS)

    assert result.predicted_value >= 0.0
