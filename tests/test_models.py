import pytest
from pydantic import ValidationError

from controller.models import PredictiveScalerSpec, PredictiveScalerStatus


def test_spec_parses_camelcase_kubernetes_payload():
    spec = PredictiveScalerSpec(
        targetDeployment="demo-app",
        minReplicas=2,
        maxReplicas=10,
        metricQuery="rate(demo_app_requests_total[2m])",
        requestsPerReplica=5,
    )
    assert spec.target_deployment == "demo-app"
    assert spec.min_replicas == 2


def test_spec_applies_defaults_when_not_given():
    spec = PredictiveScalerSpec(
        targetDeployment="demo-app",
        minReplicas=1,
        maxReplicas=5,
        metricQuery="q",
        requestsPerReplica=1,
    )
    assert spec.forecast_horizon_seconds == 300
    assert spec.confidence_threshold == 0.6
    assert spec.sync_period_seconds == 30


def test_spec_rejects_max_below_min():
    with pytest.raises(ValidationError):
        PredictiveScalerSpec(
            targetDeployment="demo-app",
            minReplicas=10,
            maxReplicas=3,
            metricQuery="q",
            requestsPerReplica=1,
        )


def test_spec_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        PredictiveScalerSpec(minReplicas=1, maxReplicas=5, metricQuery="q", requestsPerReplica=1)


def test_status_patch_dict_uses_camelcase_and_skips_unset_fields():
    status = PredictiveScalerStatus(desired_replicas=4, fallback_active=True)
    patch = status.to_patch_dict()
    assert patch == {"desiredReplicas": 4, "fallbackActive": True}
    assert "currentForecast" not in patch
