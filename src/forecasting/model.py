from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing

MIN_DATA_POINTS = 10


@dataclass
class ForecastResult:
    predicted_value: float
    confidence: float  # 0-1, how much we trust predicted_value
    reason: str  # short note on why confidence landed where it did, useful in logs/status


def forecast_load(
    points: list[tuple[datetime, float]],
    horizon_seconds: int,
    step_seconds: int,
) -> ForecastResult:
    """Predicts load `horizon_seconds` ahead of the last point in `points`.

    Confidence is not statmodels' confidence interval - Holt-Winters doesn't give
    a cheap one without running simulations on every reconcile tick. Instead we
    score confidence off how much data we have and how well the fitted curve
    tracks the recent actuals, which is a good enough proxy for "should we trust
    this" without the extra compute cost.
    """
    if len(points) < MIN_DATA_POINTS:
        return ForecastResult(
            predicted_value=0.0,
            confidence=0.0,
            reason=f"only {len(points)} data points, need at least {MIN_DATA_POINTS}",
        )

    values = np.array([v for _, v in points], dtype=float)
    series = pd.Series(values)

    # damped trend so a short traffic blip doesn't get extrapolated into infinity
    # five minutes out - protects us from wildly overscaling on noise
    model = ExponentialSmoothing(
        series,
        trend="add",
        damped_trend=True,
        initialization_method="estimated",
    )
    fitted = model.fit()

    steps_ahead = max(1, round(horizon_seconds / step_seconds))
    forecast = fitted.forecast(steps_ahead)
    predicted_value = float(forecast.iloc[-1])

    residuals = series.values - fitted.fittedvalues.values
    residual_std = float(np.std(residuals))
    mean_value = float(np.mean(values))

    if mean_value <= 0:
        return ForecastResult(0.0, 0.0, "flat/zero history, nothing to forecast from")

    # relative error - a residual std that's small compared to the average load
    # means the model is tracking the series well
    relative_error = residual_std / mean_value
    confidence = max(0.0, min(1.0, 1.0 - relative_error))

    if predicted_value < 0:
        # damped exponential smoothing can still dip negative on a falling trend;
        # negative load makes no physical sense so we clamp and dock confidence
        predicted_value = 0.0
        confidence *= 0.5

    return ForecastResult(
        predicted_value=predicted_value,
        confidence=confidence,
        reason=f"relative_error={relative_error:.3f} over {len(points)} points",
    )