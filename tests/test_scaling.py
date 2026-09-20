from forecasting.model import ForecastResult
from controller.models import PredictiveScalerSpec
from controller.scaling import compute_desired_replicas


def make_spec(**overrides):
    defaults = dict(
        targetDeployment="demo-app",
        minReplicas=1,
        maxReplicas=10,
        metricQuery="q",
        requestsPerReplica=5,
        confidenceThreshold=0.6,
    )
    defaults.update(overrides)
    return PredictiveScalerSpec(**defaults)


def test_uses_forecast_when_confidence_is_high_enough():
    spec = make_spec()
    forecast = ForecastResult(predicted_value=25, confidence=0.9, reason="test")

    decision = compute_desired_replicas(spec, forecast, current_load=1.0)

    assert decision.desired_replicas == 5  # 25 / 5 requests-per-replica
    assert decision.fallback_active is False


def test_falls_back_to_current_load_when_confidence_too_low():
    spec = make_spec()
    forecast = ForecastResult(predicted_value=100, confidence=0.1, reason="test")

    decision = compute_desired_replicas(spec, forecast, current_load=10.0)

    assert decision.desired_replicas == 2  # 10 / 5, NOT the untrusted forecast of 100
    assert decision.fallback_active is True


def test_clamps_to_max_replicas():
    spec = make_spec(maxReplicas=3)
    forecast = ForecastResult(predicted_value=100, confidence=0.9, reason="test")

    decision = compute_desired_replicas(spec, forecast, current_load=1.0)

    assert decision.desired_replicas == 3


def test_clamps_to_min_replicas():
    spec = make_spec(minReplicas=2)
    forecast = ForecastResult(predicted_value=1, confidence=0.9, reason="test")

    decision = compute_desired_replicas(spec, forecast, current_load=1.0)

    assert decision.desired_replicas == 2


def test_rounds_up_partial_replicas():
    spec = make_spec(requestsPerReplica=5)
    forecast = ForecastResult(predicted_value=11, confidence=0.9, reason="test")

    decision = compute_desired_replicas(spec, forecast, current_load=1.0)

    assert decision.desired_replicas == 3  # ceil(11 / 5) = ceil(2.2) = 3
