from controller.models import PredictiveScalerSpec
from controller.scaling import compute_desired_replicas
from forecasting.model import ForecastResult

SPEC = PredictiveScalerSpec(
    targetDeployment="demo-app",
    minReplicas=1,
    maxReplicas=10,
    metricQuery="sum(rate(demo_app_requests_total[1m]))",
    requestsPerReplica=3.5,
)


def forecast(value: float, confidence: float = 0.9) -> ForecastResult:
    return ForecastResult(predicted_value=value, confidence=confidence, reason="test")


def test_trusted_forecast_below_current_load_does_not_remove_capacity():
    decision = compute_desired_replicas(SPEC, forecast(5.0), current_load=28.0)

    assert decision.desired_replicas == 8  # ceil(28 / 3.5), not ceil(5 / 3.5) = 2
    assert decision.fallback_active is False
    assert "current load" in decision.reason


def test_trusted_forecast_above_current_load_still_scales_ahead():
    decision = compute_desired_replicas(SPEC, forecast(21.0), current_load=7.0)

    assert decision.desired_replicas == 6  # ceil(21 / 3.5)


def test_low_confidence_still_uses_current_load():
    decision = compute_desired_replicas(SPEC, forecast(50.0, confidence=0.1), current_load=7.0)

    assert decision.desired_replicas == 2
    assert decision.fallback_active is True