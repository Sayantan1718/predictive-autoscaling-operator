from __future__ import annotations

import math
from dataclasses import dataclass

from forecasting.model import ForecastResult
from operator.models import PredictiveScalerSpec


@dataclass
class ScalingDecision:
    desired_replicas: int
    fallback_active: bool
    reason: str


def compute_desired_replicas(
    spec: PredictiveScalerSpec,
    forecast: ForecastResult,
    current_load: float,
) -> ScalingDecision:
    """Turns either a trusted forecast or, in low-confidence cases, the current
    load into a replica count clamped to the CRD's min/max bounds.
    """
    if forecast.confidence < spec.confidence_threshold:
        # not confident in the forecast - fall back to reacting to load right
        # now, same spirit as a standard HPA
        load_to_use = current_load
        fallback_active = True
        reason = (
            f"forecast confidence {forecast.confidence:.2f} below threshold "
            f"{spec.confidence_threshold:.2f} ({forecast.reason}), using current load"
        )
    else:
        load_to_use = forecast.predicted_value
        fallback_active = False
        reason = f"using forecast: {forecast.reason}"

    raw_replicas = math.ceil(load_to_use / spec.requests_per_replica)
    desired = max(spec.min_replicas, min(spec.max_replicas, raw_replicas))

    if desired != raw_replicas:
        reason += f" (clamped {raw_replicas} -> {desired} by min/max bounds)"

    return ScalingDecision(desired_replicas=desired, fallback_active=fallback_active, reason=reason)